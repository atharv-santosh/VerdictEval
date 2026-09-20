"""aeh: pytest for agents. Cohesive CLI over recording, replay, scoring, and gating."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from aeh.agent import run_task
from aeh.backends.base import ModelBackend
from aeh.backends.fake import FakeModelBackend
from aeh.baseline import from_flakiness_reports, load_baseline, save_baseline
from aeh.calibrate import calibrate as run_calibration
from aeh.calibrate import load_calibration_set
from aeh.cassette import OnDivergence
from aeh.events import write_jsonl
from aeh.flakiness import SeedOutcome, analyze
from aeh.gate import diff_against_baseline, has_regression
from aeh.report import TaskReportRow, render_html, render_terminal
from aeh.scoring.assertions import evaluate_assertions
from aeh.scoring.judge import JudgeBackend, OfflineRubricJudge
from aeh.suite import load_suite

_GEMINI_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}

_BACKEND_OPTION = click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["fake", "gemini"]),
    default="fake",
    help="'fake' (default, deterministic, offline) or 'gemini' (real Vertex AI call -- opt-in only).",
)
_MODEL_OPTION = click.option("--model", default="gemini-2.5-flash-lite", help="Model id, for --backend gemini.")
_TEMPERATURE_OPTION = click.option(
    "--temperature", type=float, default=0.0, help="Sampling temperature, for --backend gemini."
)


def _cassette_path(cassette_dir: str, task_id: str, seed: int) -> str:
    return str(Path(cassette_dir) / f"{task_id}-seed{seed}.jsonl")


def _make_backend(backend_name: str, model: str, temperature: float) -> ModelBackend:
    if backend_name == "fake":
        return FakeModelBackend()
    from aeh.backends.gemini import GeminiBackend

    return GeminiBackend(model=model, temperature=temperature)


def _token_totals(events: list[Any]) -> tuple[int, int]:
    tokens_in = tokens_out = 0
    for event in events:
        if event.type == "llm_result":
            tokens_in += int(event.payload["response"].get("tokens_in", 0))
            tokens_out += int(event.payload["response"].get("tokens_out", 0))
    return tokens_in, tokens_out


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    prices = _GEMINI_PRICE_PER_MTOK.get(model)
    if prices is None:
        return None
    in_price, out_price = prices
    return (tokens_in / 1_000_000) * in_price + (tokens_out / 1_000_000) * out_price


@click.group()
def main() -> None:
    """aeh: record, replay, score, and regression-gate LLM agent trajectories."""


@main.command()
@click.option("--suite", "suite_path", required=True, type=click.Path(exists=True))
@click.option("--task", "task_id", required=False, help="Task id; omit to record every task/seed in the suite.")
@click.option("--seed", type=int, required=False)
@click.option("--out", "out_dir", required=True, type=click.Path(), help="Directory to write cassette JSONL files into.")
@_BACKEND_OPTION
@_MODEL_OPTION
@_TEMPERATURE_OPTION
def record(
    suite_path: str,
    task_id: str | None,
    seed: int | None,
    out_dir: str,
    backend_name: str,
    model: str,
    temperature: float,
) -> None:
    """Run task(s) live and journal a cassette per (task, seed)."""
    suite = load_suite(suite_path)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    tasks = [suite.get(task_id)] if task_id else suite.tasks
    backend = _make_backend(backend_name, model, temperature)
    count = 0
    tokens_in_total = tokens_out_total = 0
    for task in tasks:
        seeds = [seed] if seed is not None else task.seeds
        for s in seeds:
            result = run_task(task, s, mode="record", backend=backend)
            write_jsonl(_cassette_path(out_dir, task.id, s), result.events)
            ti, to = _token_totals(result.events)
            tokens_in_total += ti
            tokens_out_total += to
            count += 1
    click.echo(f"recorded {count} cassette(s) to {out_dir} (backend={backend_name})")
    if backend_name != "fake":
        cost = _estimate_cost(model, tokens_in_total, tokens_out_total)
        cost_str = f", est. cost=${cost:.4f}" if cost is not None else ""
        click.echo(f"tokens: in={tokens_in_total} out={tokens_out_total}{cost_str}")


@main.command()
@click.option("--suite", "suite_path", required=True, type=click.Path(exists=True))
@click.option("--cassette-dir", required=True, type=click.Path(exists=True))
@click.option("--task", "task_id", required=False)
@click.option("--seed", type=int, required=False)
@click.option("--on-divergence", type=click.Choice(["fail", "record", "passthrough"]), default="fail")
@_BACKEND_OPTION
@_MODEL_OPTION
@_TEMPERATURE_OPTION
def replay(
    suite_path: str,
    cassette_dir: str,
    task_id: str | None,
    seed: int | None,
    on_divergence: str,
    backend_name: str,
    model: str,
    temperature: float,
) -> None:
    """Replay task(s) from cassettes, verifying determinism against the recorded trace."""
    suite = load_suite(suite_path)
    tasks = [suite.get(task_id)] if task_id else suite.tasks
    policy = OnDivergence(on_divergence)
    backend = _make_backend(backend_name, model, temperature)
    ok = 0
    diverged = 0
    for task in tasks:
        seeds = [seed] if seed is not None else task.seeds
        for s in seeds:
            path = _cassette_path(cassette_dir, task.id, s)
            result = run_task(task, s, mode="replay", cassette_path=path, on_divergence=policy, backend=backend)
            is_divergence = result.diverged or bool(result.error and "divergence" in result.error.lower())
            status = "DIVERGED" if is_divergence else ("ERROR" if result.error else "ok")
            if is_divergence:
                diverged += 1
            else:
                ok += 1
            click.echo(f"{task.id} seed={s}: {status}" + (f" ({result.error})" if result.error else ""))
    click.echo(f"replayed: {ok} clean, {diverged} diverged")
    if diverged and policy == OnDivergence.FAIL:
        sys.exit(1)


def _run_suite(
    suite_path: str, cassette_dir: str, mode: str, backend: ModelBackend | None = None
) -> dict[str, Any]:
    suite = load_suite(suite_path)
    Path(cassette_dir).mkdir(parents=True, exist_ok=True)
    tasks_out = []
    tokens_in_total = tokens_out_total = 0
    for task in suite.tasks:
        seed_rows = []
        for seed in task.seeds:
            path = _cassette_path(cassette_dir, task.id, seed)
            if mode == "record":
                result = run_task(task, seed, mode="record", backend=backend)
                write_jsonl(path, result.events)
            else:
                result = run_task(task, seed, mode="replay", cassette_path=path, backend=backend)
            ti, to = _token_totals(result.events)
            tokens_in_total += ti
            tokens_out_total += to
            assertion_results = evaluate_assertions(result.final_state, result.tool_sequence, task.assertions)
            passed = result.error is None and all(a.passed for a in assertion_results)
            seed_rows.append(
                {
                    "seed": seed,
                    "passed": passed,
                    "finished": result.finished,
                    "flake_triggered": result.flake_triggered,
                    "diverged": result.diverged,
                    "error": result.error,
                    "tool_sequence": result.tool_sequence,
                    "final_state": result.final_state,
                    "assertions": [{"type": a.spec_type, "passed": a.passed, "message": a.message} for a in assertion_results],
                }
            )
        tasks_out.append({"task_id": task.id, "tags": task.tags, "rubric": task.rubric, "seeds": seed_rows})
    return {
        "suite_name": suite.name,
        "tasks": tasks_out,
        "tokens_in": tokens_in_total,
        "tokens_out": tokens_out_total,
    }


@main.command()
@click.option("--suite", "suite_path", required=True, type=click.Path(exists=True))
@click.option("--cassette-dir", required=True, type=click.Path())
@click.option("--mode", type=click.Choice(["record", "replay"]), default="record")
@click.option("--out", "out_path", required=True, type=click.Path())
@_BACKEND_OPTION
@_MODEL_OPTION
@_TEMPERATURE_OPTION
def run(
    suite_path: str,
    cassette_dir: str,
    mode: str,
    out_path: str,
    backend_name: str,
    model: str,
    temperature: float,
) -> None:
    """Run every task/seed in a suite and write a results.json for report/baseline/gate."""
    backend = _make_backend(backend_name, model, temperature)
    results = _run_suite(suite_path, cassette_dir, mode, backend=backend)
    Path(out_path).write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    n_tasks = len(results["tasks"])
    n_seeds = sum(len(t["seeds"]) for t in results["tasks"])
    click.echo(f"ran {n_tasks} tasks / {n_seeds} seed-runs ({mode} mode, backend={backend_name}) -> {out_path}")
    if backend_name != "fake":
        cost = _estimate_cost(model, results["tokens_in"], results["tokens_out"])
        cost_str = f", est. cost=${cost:.4f}" if cost is not None else ""
        click.echo(f"tokens: in={results['tokens_in']} out={results['tokens_out']}{cost_str}")


def _flakiness_from_results(results: dict[str, Any]) -> list:
    outcomes = [
        SeedOutcome(task_id=t["task_id"], seed=s["seed"], passed=s["passed"])
        for t in results["tasks"]
        for s in t["seeds"]
    ]
    return analyze(outcomes)


@main.command()
@click.option("--results", "results_path", required=True, type=click.Path(exists=True))
@click.option("--baseline", "baseline_path", required=False, type=click.Path(exists=True))
@click.option("--calibration", "calibration_path", required=False, type=click.Path(exists=True))
@click.option("--html-out", required=False, type=click.Path())
def report(results_path: str, baseline_path: str | None, calibration_path: str | None, html_out: str | None) -> None:
    """Print a terminal report and optionally write a self-contained HTML report."""
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))
    reports = _flakiness_from_results(results)
    tags_by_id = {t["task_id"]: t["tags"] for t in results["tasks"]}
    rows = [
        TaskReportRow(r.task_id, r.n, r.passes, r.pass_rate, r.flake_score, r.verdict, tags_by_id.get(r.task_id, []))
        for r in reports
    ]
    diffs = None
    if baseline_path:
        baseline = load_baseline(baseline_path)
        diffs = diff_against_baseline(baseline, reports)
    calibration = None
    if calibration_path:
        calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))

    click.echo(render_terminal(results["suite_name"], rows, diffs, calibration))
    if html_out:
        Path(html_out).write_text(render_html(results["suite_name"], rows, diffs, calibration), encoding="utf-8")
        click.echo(f"\nwrote HTML report to {html_out}")


@main.command()
@click.option("--data", "data_path", required=True, type=click.Path(exists=True))
@click.option("--out", "out_path", required=False, type=click.Path())
@click.option(
    "--backend",
    "backend_name",
    type=click.Choice(["fake", "gemini"]),
    default="fake",
    help="'fake' (default): deterministic OfflineRubricJudge. 'gemini': real Vertex AI judge -- opt-in only.",
)
@_MODEL_OPTION
def calibrate(data_path: str, out_path: str | None, backend_name: str, model: str) -> None:
    """Measure judge-vs-human agreement on a labeled trajectory set."""
    rows = load_calibration_set(data_path)
    judge: JudgeBackend
    if backend_name == "gemini":
        from aeh.scoring.judge import GeminiJudge

        judge = GeminiJudge(model=model)
    else:
        judge = OfflineRubricJudge()
    result = run_calibration(judge, rows)
    click.echo(f"n={result.n}  raw_agreement={result.raw_agreement:.3f}  kappa={result.kappa:.3f}")
    click.echo(f"confusion matrix (rows=human, cols=judge): {json.dumps(result.confusion, sort_keys=True)}")
    if result.disagreements:
        click.echo(f"\n{len(result.disagreements)} disagreement(s):")
        for d in result.disagreements:
            click.echo(f"  {d['id']}: human={d['human_label']} judge={d['judge_label']} ({d['rationale']})")
    if backend_name == "gemini":
        cost = _estimate_cost(model, judge.tokens_in, judge.tokens_out)  # type: ignore[union-attr]
        cost_str = f", est. cost=${cost:.4f}" if cost is not None else ""
        click.echo(f"tokens: in={judge.tokens_in} out={judge.tokens_out}{cost_str}")  # type: ignore[union-attr]
    if out_path:
        Path(out_path).write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


@main.group()
def baseline() -> None:
    """Save or inspect baseline result snapshots."""


@baseline.command("save")
@click.option("--results", "results_path", required=True, type=click.Path(exists=True))
@click.option("--out", "out_path", required=True, type=click.Path())
def baseline_save(results_path: str, out_path: str) -> None:
    """Snapshot a results.json's per-task pass rates as a new baseline."""
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))
    reports = _flakiness_from_results(results)
    baseline_obj = from_flakiness_reports(results["suite_name"], reports)
    save_baseline(out_path, baseline_obj)
    click.echo(f"saved baseline for {len(baseline_obj.tasks)} tasks to {out_path}")


@main.command()
@click.option("--baseline", "baseline_path", required=True, type=click.Path(exists=True))
@click.option("--results", "results_path", required=True, type=click.Path(exists=True))
@click.option("--z", default=1.96, help="Tolerance in standard errors of the baseline pass rate.")
def gate(baseline_path: str, results_path: str, z: float) -> None:
    """Diff a run against a baseline and exit non-zero if any task regressed."""
    baseline_obj = load_baseline(baseline_path)
    results = json.loads(Path(results_path).read_text(encoding="utf-8"))
    reports = _flakiness_from_results(results)
    diffs = diff_against_baseline(baseline_obj, reports, z=z)
    for d in diffs:
        b = f"{d.baseline_pass_rate:.2f}" if d.baseline_pass_rate is not None else "-"
        c = f"{d.current_pass_rate:.2f}" if d.current_pass_rate is not None else "-"
        marker = "  <-- REGRESSION" if d.classification == "regressed" else ""
        click.echo(f"{d.task_id:<28} baseline={b:>5} current={c:>5}  margin={d.margin:.3f}  {d.classification}{marker}")
    if has_regression(diffs):
        click.echo("\nGATE FAILED: at least one task regressed beyond tolerance.")
        sys.exit(1)
    click.echo("\nGATE PASSED: no regressions beyond tolerance.")


@main.command()
@click.option("--port", default=8712, type=int, help="Port to bind to.")
@click.option("--host", default="127.0.0.1", help="Host to bind to (default: localhost only).")
@click.option("--suite", "suite_path", default="demo/suite.yaml", type=click.Path(), help="Suite YAML to open.")
@click.option(
    "--cassette-dir",
    default="cassettes/gemini-flakiness",
    type=click.Path(),
    help="Directory of recorded cassettes for the cassette browser.",
)
@click.option(
    "--work-dir",
    default=".aeh-studio",
    type=click.Path(),
    help="Scratch directory for run artifacts and the current baseline.",
)
@click.option(
    "--calibration",
    "calibration_path",
    default="data/calibration_set.jsonl",
    type=click.Path(),
    help="Human-labeled calibration set for the judge calibration screen.",
)
def serve(port: int, host: str, suite_path: str, cassette_dir: str, work_dir: str, calibration_path: str) -> None:
    """Launch Eval Studio: a local web app to author, run, and inspect evals interactively."""
    try:
        import uvicorn
    except ImportError:
        click.echo(
            "Eval Studio requires the optional 'web' extra: install with `uv pip install -e '.[web]'`",
            err=True,
        )
        sys.exit(1)

    from aeh.web.server import create_app

    app = create_app(
        suite_path=suite_path,
        cassette_dir=cassette_dir,
        work_dir=work_dir,
        calibration_path=calibration_path,
    )
    click.echo(f"Eval Studio running at http://{host}:{port}  (suite: {suite_path})")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
