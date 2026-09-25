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
from aeh.metrics import cost_per_solved, estimate_cost, fmt_cost, summarize
from aeh.report import TaskReportRow, render_html, render_terminal, render_totals
from aeh.scoring.assertions import evaluate_assertions
from aeh.scoring.judge import DEFAULT_PROMPT_VERSION, PROMPT_VERSIONS, JudgeBackend, OfflineRubricJudge
from aeh.suite import load_suite

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


def _cost_latency_line(results: dict[str, Any]) -> str:
    """One-line tokens/dollars/latency summary of a results.json."""
    cl = results["cost_latency"]
    solved = results["solved_seed_runs"]
    per_solved = results["cost_per_solved_usd"]
    per_solved_str = f"{fmt_cost(per_solved)}/solved task" if per_solved is not None else "no cost per solved task"
    return (
        f"tokens: in={cl['tokens_in']} out={cl['tokens_out']}  cost={fmt_cost(cl['cost_usd'])}  "
        f"wall clock p50={cl['p50_s']:.2f}s p95={cl['p95_s']:.2f}s  "
        f"solved {solved}/{results['total_seed_runs']}  {per_solved_str}"
    )


def _cassette_path(cassette_dir: str, task_id: str, seed: int) -> str:
    return str(Path(cassette_dir) / f"{task_id}-seed{seed}.jsonl")


def _make_backend(backend_name: str, model: str, temperature: float) -> ModelBackend:
    if backend_name == "fake":
        return FakeModelBackend()
    from aeh.backends.gemini import GeminiBackend

    return GeminiBackend(model=model, temperature=temperature)




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
            tokens_in_total += result.tokens_in
            tokens_out_total += result.tokens_out
            count += 1
    click.echo(f"recorded {count} cassette(s) to {out_dir} (backend={backend_name})")
    if backend_name != "fake":
        cost = estimate_cost(model, tokens_in_total, tokens_out_total)
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
    suite_path: str,
    cassette_dir: str,
    mode: str,
    backend: ModelBackend | None = None,
    model: str = "",
    priced: bool = False,
) -> dict[str, Any]:
    suite = load_suite(suite_path)
    Path(cassette_dir).mkdir(parents=True, exist_ok=True)
    tasks_out = []
    all_seed_rows: list[dict[str, Any]] = []
    for task in suite.tasks:
        seed_rows = []
        for seed in task.seeds:
            path = _cassette_path(cassette_dir, task.id, seed)
            if mode == "record":
                result = run_task(task, seed, mode="record", backend=backend)
                write_jsonl(path, result.events)
            else:
                result = run_task(task, seed, mode="replay", cassette_path=path, backend=backend)
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
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "wall_clock_s": result.wall_clock_s,
                }
            )
        all_seed_rows.extend(seed_rows)
        tasks_out.append(
            {
                "task_id": task.id,
                "tags": task.tags,
                "rubric": task.rubric,
                "seeds": seed_rows,
                "cost_latency": summarize(seed_rows, model, priced=priced).to_dict(),
            }
        )
    suite_cl = summarize(all_seed_rows, model, priced=priced)
    solved = sum(1 for r in all_seed_rows if r["passed"])
    return {
        "suite_name": suite.name,
        "model": model,
        "priced": priced,
        "tasks": tasks_out,
        "tokens_in": suite_cl.tokens_in,
        "tokens_out": suite_cl.tokens_out,
        "cost_latency": suite_cl.to_dict(),
        "solved_seed_runs": solved,
        "total_seed_runs": len(all_seed_rows),
        "cost_per_solved_usd": cost_per_solved(suite_cl.cost_usd, solved),
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
    priced = backend_name != "fake"
    results = _run_suite(
        suite_path,
        cassette_dir,
        mode,
        backend=backend,
        model=model if priced else "fake-backend (offline, no model called)",
        priced=priced,
    )
    results["backend"] = backend_name
    Path(out_path).write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    n_tasks = len(results["tasks"])
    click.echo(
        f"ran {n_tasks} tasks / {results['total_seed_runs']} seed-runs "
        f"({mode} mode, backend={backend_name}) -> {out_path}"
    )
    click.echo(_cost_latency_line(results))


def _totals(results: dict[str, Any]) -> dict[str, Any] | None:
    """The suite-level cost/latency block, for results.json files that carry one."""
    if "cost_latency" not in results:
        return None
    return {
        "model": results.get("model"),
        "cost_latency": results["cost_latency"],
        "solved_seed_runs": results["solved_seed_runs"],
        "total_seed_runs": results["total_seed_runs"],
        "cost_per_solved_usd": results.get("cost_per_solved_usd"),
    }


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
    by_id = {t["task_id"]: t for t in results["tasks"]}
    rows = []
    for r in reports:
        task = by_id.get(r.task_id, {})
        cl = task.get("cost_latency", {})
        rows.append(
            TaskReportRow(
                r.task_id,
                r.n,
                r.passes,
                r.pass_rate,
                r.flake_score,
                r.verdict,
                task.get("tags", []),
                tokens_in=cl.get("tokens_in", 0),
                tokens_out=cl.get("tokens_out", 0),
                cost_usd=cl.get("cost_usd"),
                p50_s=cl.get("p50_s", 0.0),
                p95_s=cl.get("p95_s", 0.0),
            )
        )
    totals = _totals(results)
    diffs = None
    if baseline_path:
        baseline = load_baseline(baseline_path)
        diffs = diff_against_baseline(baseline, reports)
    calibration = None
    if calibration_path:
        calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))

    click.echo(render_terminal(results["suite_name"], rows, diffs, calibration, totals))
    if html_out:
        Path(html_out).write_text(
            render_html(results["suite_name"], rows, diffs, calibration, totals), encoding="utf-8"
        )
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
@click.option(
    "--split",
    type=click.Choice(["all", "tune", "holdout"]),
    default="all",
    help="'tune': the half the judge prompt was developed against. 'holdout': the reserved half (the headline number).",
)
@click.option(
    "--prompt-version",
    type=click.Choice(sorted(PROMPT_VERSIONS)),
    default=DEFAULT_PROMPT_VERSION,
    help="Judge prompt variant, for --backend gemini.",
)
def calibrate(
    data_path: str, out_path: str | None, backend_name: str, model: str, split: str, prompt_version: str
) -> None:
    """Measure judge-vs-human agreement on a labeled trajectory set."""
    rows = load_calibration_set(data_path, split=split)
    judge: JudgeBackend
    if backend_name == "gemini":
        from aeh.scoring.judge import GeminiJudge

        judge = GeminiJudge(model=model, prompt_version=prompt_version)
    else:
        judge = OfflineRubricJudge()
    result = run_calibration(judge, rows, split=split)
    click.echo(
        f"split={split}  n={result.n}  raw_agreement={result.raw_agreement:.3f}  "
        f"kappa={result.kappa:.3f}  95% CI [{result.kappa_ci_low:.3f}, {result.kappa_ci_high:.3f}]"
    )
    click.echo(f"confusion matrix (rows=human, cols=judge): {json.dumps(result.confusion, sort_keys=True)}")
    if result.disagreements:
        click.echo(f"\n{len(result.disagreements)} disagreement(s):")
        for d in result.disagreements:
            click.echo(f"  {d['id']}: human={d['human_label']} judge={d['judge_label']} ({d['rationale']})")
    if backend_name == "gemini":
        cost = estimate_cost(model, judge.tokens_in, judge.tokens_out)  # type: ignore[union-attr]
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
    totals = _totals(results)
    if totals:
        click.echo("")
        click.echo(render_totals(totals))
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
