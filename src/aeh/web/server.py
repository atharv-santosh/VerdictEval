"""Eval Studio: FastAPI app that lets a person author, run, and inspect aeh evals interactively in a browser."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from aeh.agent import run_task
from aeh.backends.base import ModelBackend
from aeh.backends.fake import FakeModelBackend
from aeh.baseline import from_flakiness_reports, load_baseline, save_baseline
from aeh.calibrate import calibrate as run_calibration
from aeh.calibrate import load_calibration_set
from aeh.cassette import DivergenceError, OnDivergence
from aeh.flakiness import SeedOutcome, TaskFlakinessReport, analyze
from aeh.gate import diff_against_baseline
from aeh.scoring.assertions import evaluate_assertions
from aeh.scoring.judge import GeminiJudge, JudgeBackend, OfflineRubricJudge
from aeh.suite import Suite, Task, load_suite

_WEB_DIR = Path(__file__).resolve().parent

_ASSERTION_TYPES = ("file_contains", "file_equals", "tool_sequence", "forbidden_tool", "status_equals")
_TOOL_NAMES = ("list_files", "read_file", "write_file", "search")

_GEMINI_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}

_ESTIMATED_TOKENS_IN_PER_SEED = 900
_ESTIMATED_TOKENS_OUT_PER_SEED = 120


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    prices = _GEMINI_PRICE_PER_MTOK.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    return (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out


def _token_totals(events: list[Any]) -> tuple[int, int]:
    tokens_in = tokens_out = 0
    for event in events:
        if event.type == "llm_result":
            tokens_in += int(event.payload["response"].get("tokens_in", 0))
            tokens_out += int(event.payload["response"].get("tokens_out", 0))
    return tokens_in, tokens_out


def _event_to_dict(event: Any) -> dict[str, Any]:
    return {"seq": event.seq, "ts": event.ts, "type": event.type, "payload": event.payload}


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "input": task.input,
        "setup": task.setup,
        "plan": task.plan,
        "flaky": task.flaky,
        "assertions": task.assertions,
        "rubric": task.rubric,
        "seeds": task.seeds,
        "tags": task.tags,
    }


def validate_task_dict(data: dict[str, Any], existing_ids: set[str], editing_id: str | None) -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors: list[str] = []

    task_id = (data.get("id") or "").strip()
    if not task_id:
        errors.append("Task ID is required.")
    elif not all(c.isalnum() or c in "_-" for c in task_id):
        errors.append("Task ID may only contain letters, digits, '_' and '-'.")
    elif task_id in existing_ids and task_id != editing_id:
        errors.append(f"Task ID {task_id!r} already exists.")

    if not (data.get("input") or "").strip():
        errors.append("Prompt (input) is required.")

    setup = data.get("setup") or {}
    workspace = setup.get("workspace", {}) if isinstance(setup, dict) else None
    if workspace is None or not isinstance(workspace, dict):
        errors.append("Setup workspace must be a map of path -> file contents.")
    else:
        for path, content in workspace.items():
            if not isinstance(path, str) or not path.strip():
                errors.append("Every workspace file needs a non-empty path.")
            if not isinstance(content, str):
                errors.append(f"Workspace file {path!r} contents must be a string.")

    plan = data.get("plan") or []
    if not isinstance(plan, list):
        errors.append("Plan must be a list of steps.")
    else:
        for i, step in enumerate(plan):
            if not isinstance(step, dict):
                errors.append(f"Plan step {i + 1} must be an object.")
                continue
            if "tool" in step:
                if step["tool"] not in _TOOL_NAMES:
                    errors.append(f"Plan step {i + 1}: unknown tool {step['tool']!r}.")
                if not isinstance(step.get("args"), dict):
                    errors.append(f"Plan step {i + 1}: 'args' must be an object.")
            elif "finish" in step:
                if not isinstance(step["finish"], dict) or "status" not in step["finish"]:
                    errors.append(f"Plan step {i + 1}: 'finish' needs a 'status'.")
            else:
                errors.append(f"Plan step {i + 1}: needs either 'tool' or 'finish'.")
        if plan and not any("finish" in s for s in plan if isinstance(s, dict)):
            errors.append("Plan must end with a 'finish' step (the fake backend never loops on its own).")

    assertions = data.get("assertions") or []
    if not isinstance(assertions, list):
        errors.append("Assertions must be a list.")
    else:
        for i, spec in enumerate(assertions):
            if not isinstance(spec, dict) or "type" not in spec:
                errors.append(f"Assertion {i + 1}: needs a 'type'.")
                continue
            t = spec["type"]
            if t not in _ASSERTION_TYPES:
                errors.append(f"Assertion {i + 1}: unknown type {t!r}.")
                continue
            required = {
                "file_contains": ("path", "text"),
                "file_equals": ("path", "content"),
                "tool_sequence": ("sequence",),
                "forbidden_tool": ("tool",),
                "status_equals": ("status",),
            }[t]
            for field_name in required:
                if field_name not in spec:
                    errors.append(f"Assertion {i + 1} ({t}): missing {field_name!r}.")

    seeds = data.get("seeds") or []
    if not isinstance(seeds, list) or not seeds:
        errors.append("Seeds must be a non-empty list of integers.")
    else:
        for s in seeds:
            if not isinstance(s, int):
                errors.append("Every seed must be an integer.")
                break

    return errors


def dict_to_task(data: dict[str, Any]) -> Task:
    return Task(
        id=data["id"].strip(),
        input=data["input"],
        setup=data.get("setup") or {},
        plan=data.get("plan") or [],
        flaky=data.get("flaky"),
        assertions=data.get("assertions") or [],
        rubric=data.get("rubric", ""),
        seeds=[int(s) for s in (data.get("seeds") or [0])],
        tags=data.get("tags") or [],
    )


def suite_to_yaml_dict(suite: Suite) -> dict[str, Any]:
    return {"name": suite.name, "tasks": [task_to_dict(t) for t in suite.tasks]}


@dataclass
class RunJob:
    id: str
    suite_name: str
    task_ids: list[str]
    seeds_override: list[int] | None
    backend_name: str
    model: str
    temperature: float
    use_judge: bool
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    listeners: list["queue.Queue[str]"] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    total_seed_runs: int = 0
    completed_seed_runs: int = 0

    def broadcast(self, event: dict[str, Any]) -> None:
        line = json.dumps(event)
        with self.lock:
            listeners = list(self.listeners)
        for q in listeners:
            q.put(line)

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "suite_name": self.suite_name,
            "status": self.status,
            "backend": self.backend_name,
            "model": self.model,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "total_seed_runs": self.total_seed_runs,
            "completed_seed_runs": self.completed_seed_runs,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_estimate": _estimate_cost(self.model, self.tokens_in, self.tokens_out)
            if self.backend_name != "fake"
            else 0.0,
        }

    def flakiness_reports(self) -> list[TaskFlakinessReport]:
        outcomes = [
            SeedOutcome(task_id=tid, seed=int(seed), passed=row["passed"])
            for tid, seeds in self.tasks.items()
            for seed, row in seeds.items()
        ]
        return analyze(outcomes)

    def to_grid(self) -> dict[str, Any]:
        reports = {r.task_id: r for r in self.flakiness_reports()}
        rows = []
        for task_id, seeds in self.tasks.items():
            report = reports.get(task_id)
            rows.append(
                {
                    "task_id": task_id,
                    "seeds": {str(seed): row for seed, row in sorted(seeds.items(), key=lambda kv: int(kv[0]))},
                    "n": report.n if report else 0,
                    "passes": report.passes if report else 0,
                    "pass_rate": report.pass_rate if report else 0.0,
                    "flake_score": report.flake_score if report else 0.0,
                    "verdict": report.verdict if report else "unknown",
                    "pass_at_k": report.pass_at_k if report else {},
                }
            )
        return {"summary": self.to_summary(), "rows": rows}


class WebState:
    """All mutable, in-process state for the Eval Studio server."""

    def __init__(self, suite_path: str, cassette_dir: str, work_dir: str, calibration_path: str) -> None:
        self.suite_path = Path(suite_path)
        self.cassette_dir = Path(cassette_dir)
        self.work_dir = Path(work_dir)
        self.calibration_path = Path(calibration_path)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_path = self.work_dir / "baseline.json"

        self.suite_lock = threading.Lock()
        self.suite = load_suite(self.suite_path)

        self.runs_lock = threading.Lock()
        self.runs: dict[str, RunJob] = {}
        self.run_order: list[str] = []

        self.calibration_rows = load_calibration_set(self.calibration_path)
        self.calibration_lock = threading.Lock()
        self.calibration_overrides: dict[str, str] = {}

    def reload_suite(self) -> None:
        with self.suite_lock:
            self.suite = load_suite(self.suite_path)

    def save_suite(self) -> None:
        with self.suite_lock:
            data = suite_to_yaml_dict(self.suite)
            self.suite_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def upsert_task(self, task: Task, editing_id: str | None) -> None:
        with self.suite_lock:
            tasks = list(self.suite.tasks)
            if editing_id is not None:
                tasks = [t for t in tasks if t.id != editing_id]
            tasks = [t for t in tasks if t.id != task.id]
            tasks.append(task)
            self.suite = Suite(name=self.suite.name, tasks=tasks)
        self.save_suite()

    def delete_task(self, task_id: str) -> None:
        with self.suite_lock:
            self.suite = Suite(name=self.suite.name, tasks=[t for t in self.suite.tasks if t.id != task_id])
        self.save_suite()

    def gemini_status(self) -> dict[str, Any]:
        import os

        try:
            import google.genai  # noqa: F401

            installed = True
        except ImportError:
            installed = False
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        return {
            "installed": installed,
            "project": project,
            "location": os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
            "ready": installed and bool(project),
        }

    def make_backend(self, backend_name: str, model: str, temperature: float) -> ModelBackend:
        if backend_name == "fake":
            return FakeModelBackend()
        from aeh.backends.gemini import GeminiBackend

        return GeminiBackend(model=model, temperature=temperature)

    def start_run(
        self,
        task_ids: list[str],
        seeds_override: list[int] | None,
        backend_name: str,
        model: str,
        temperature: float,
        use_judge: bool,
    ) -> RunJob:
        tasks = [self.suite.get(tid) for tid in task_ids]
        run_id = uuid.uuid4().hex[:10]
        job = RunJob(
            id=run_id,
            suite_name=self.suite.name,
            task_ids=task_ids,
            seeds_override=seeds_override,
            backend_name=backend_name,
            model=model,
            temperature=temperature,
            use_judge=use_judge,
        )
        job.total_seed_runs = sum(len(seeds_override or t.seeds) for t in tasks)
        with self.runs_lock:
            self.runs[run_id] = job
            self.run_order.append(run_id)
        thread = threading.Thread(target=self._execute_run, args=(job, tasks), daemon=True)
        thread.start()
        return job

    def _execute_run(self, job: RunJob, tasks: list[Task]) -> None:
        try:
            backend = self.make_backend(job.backend_name, job.model, job.temperature)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = _friendly_backend_error(exc)
            job.finished_at = time.time()
            job.broadcast({"type": "run_error", "error": job.error})
            return

        judge: JudgeBackend | None = None
        if job.use_judge:
            try:
                judge = GeminiJudge(model=job.model) if job.backend_name == "gemini" else OfflineRubricJudge()
            except Exception:  # noqa: BLE001
                judge = OfflineRubricJudge()

        job.broadcast({"type": "run_start", "run_id": job.id, "total": job.total_seed_runs})
        try:
            for task in tasks:
                job.tasks.setdefault(task.id, {})
                seeds = job.seeds_override or task.seeds
                for seed in seeds:
                    job.broadcast({"type": "seed_start", "task_id": task.id, "seed": seed})
                    row = self._run_one_seed(job, task, seed, judge)
                    job.tasks[task.id][str(seed)] = row
                    job.completed_seed_runs += 1
                    ti, to = row["tokens_in"], row["tokens_out"]
                    job.tokens_in += ti
                    job.tokens_out += to
                    job.broadcast(
                        {
                            "type": "seed_done",
                            "task_id": task.id,
                            "seed": seed,
                            "passed": row["passed"],
                            "error": row["error"],
                            "completed": job.completed_seed_runs,
                            "total": job.total_seed_runs,
                            "tokens_in": job.tokens_in,
                            "tokens_out": job.tokens_out,
                            "cost_estimate": _estimate_cost(job.model, job.tokens_in, job.tokens_out)
                            if job.backend_name != "fake"
                            else 0.0,
                        }
                    )
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = _friendly_backend_error(exc)
            job.broadcast({"type": "run_error", "error": job.error})
        finally:
            job.finished_at = time.time()
            job.broadcast({"type": "run_end", "status": job.status, "error": job.error})

    def _run_one_seed(self, job: RunJob, task: Task, seed: int, judge: JudgeBackend | None) -> dict[str, Any]:
        cassette_path = str(self.work_dir / "runs" / job.id / f"{task.id}-seed{seed}.jsonl")
        Path(cassette_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            backend = self.make_backend(job.backend_name, job.model, job.temperature)
            result = run_task(task, seed, mode="record", backend=backend)
        except Exception as exc:  # noqa: BLE001
            friendly = _friendly_backend_error(exc)
            return {
                "seed": seed,
                "passed": False,
                "finished": False,
                "diverged": False,
                "error": friendly,
                "tool_sequence": [],
                "final_state": {},
                "assertions": [],
                "judge": None,
                "events": [],
                "tokens_in": 0,
                "tokens_out": 0,
            }

        from aeh.events import write_jsonl

        write_jsonl(cassette_path, result.events)
        ti, to = _token_totals(result.events)
        assertion_results = evaluate_assertions(result.final_state, result.tool_sequence, task.assertions)
        passed = result.error is None and all(a.passed for a in assertion_results)

        judge_out: dict[str, Any] | None = None
        if judge is not None:
            hint = _derive_rubric_hint(task)
            try:
                verdict = judge.score(hint, result.final_state)
                judge_out = {"label": verdict.label, "rationale": verdict.rationale}
            except Exception as exc:  # noqa: BLE001
                judge_out = {"label": "error", "rationale": _friendly_backend_error(exc)}

        return {
            "seed": seed,
            "passed": passed,
            "finished": result.finished,
            "diverged": result.diverged,
            "error": result.error,
            "tool_sequence": result.tool_sequence,
            "final_state": result.final_state,
            "assertions": [
                {"type": a.spec_type, "passed": a.passed, "message": a.message} for a in assertion_results
            ],
            "judge": judge_out,
            "events": [_event_to_dict(e) for e in result.events],
            "tokens_in": ti,
            "tokens_out": to,
        }


def _derive_rubric_hint(task: Task) -> dict[str, Any]:
    """Best-effort structured hint for the judge, derived from the task's own assertions (the judge module's protocol wants ``expect_substring``; a task's rubric is free text)."""
    for spec in task.assertions:
        if spec.get("type") == "file_contains":
            return {"expect_substring": spec["text"], "rubric": task.rubric}
        if spec.get("type") == "file_equals":
            return {"expect_substring": spec["content"], "rubric": task.rubric}
    return {"expect_substring": None, "rubric": task.rubric}


def _friendly_backend_error(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, DivergenceError):
        return f"Cassette divergence: the agent's request no longer matches what was recorded.\n{exc.diff}"
    lowered = message.lower()
    if "429" in message or "resource_exhausted" in lowered or "rate" in lowered:
        return (
            "Rate limited by the model provider (HTTP 429). Wait a moment and retry, or reduce "
            "concurrent seeds. Original error: " + message
        )
    if "unauthenticated" in lowered or "permission" in lowered or "credential" in lowered:
        return (
            "Authentication failed. Run `gcloud auth application-default login` and set "
            "GOOGLE_CLOUD_PROJECT, then retry. Original error: " + message
        )
    if "timeout" in lowered or "deadline" in lowered:
        return "The model call timed out. Retry, or check your network connection. Original error: " + message
    if "requires the optional" in message or "requires google_cloud_project" in lowered:
        return message
    return f"{type(exc).__name__}: {message}"


def create_app(
    suite_path: str = "demo/suite.yaml",
    cassette_dir: str = "cassettes/gemini-flakiness",
    work_dir: str = ".aeh-studio",
    calibration_path: str = "data/calibration_set.jsonl",
) -> FastAPI:
    state = WebState(suite_path, cassette_dir, work_dir, calibration_path)
    app = FastAPI(title="Eval Studio")
    app.state.aeh = state

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        return templates.TemplateResponse(request, "index.html", {})

    @app.get("/api/mode")
    def api_mode() -> dict[str, Any]:
        gemini = state.gemini_status()
        return {
            "gemini": gemini,
            "banner": (
                "Live Gemini is configured (project={0}, location={1}). Real-model runs are billed."
                .format(gemini["project"], gemini["location"])
                if gemini["ready"]
                else "Running fully offline on the fake backend. To enable real Gemini runs: install "
                "the 'gemini' extra (`uv pip install -e '.[gemini]'`), run "
                "`gcloud auth application-default login`, and set GOOGLE_CLOUD_PROJECT."
            ),
        }

    @app.get("/api/suite")
    def api_get_suite() -> dict[str, Any]:
        return {
            "name": state.suite.name,
            "path": str(state.suite_path),
            "tasks": [task_to_dict(t) for t in state.suite.tasks],
        }

    @app.post("/api/suite/tasks")
    def api_upsert_task(body: dict[str, Any]) -> Any:
        editing_id = body.get("editing_id")
        task_data = body.get("task") or {}
        existing_ids = {t.id for t in state.suite.tasks}
        errors = validate_task_dict(task_data, existing_ids, editing_id)
        if errors:
            return {"ok": False, "errors": errors}
        task = dict_to_task(task_data)
        state.upsert_task(task, editing_id)
        return {"ok": True, "task": task_to_dict(task)}

    @app.delete("/api/suite/tasks/{task_id}")
    def api_delete_task(task_id: str) -> dict[str, Any]:
        if task_id not in {t.id for t in state.suite.tasks}:
            raise HTTPException(404, f"no such task: {task_id}")
        state.delete_task(task_id)
        return {"ok": True}

    @app.post("/api/suite/reload")
    def api_reload_suite() -> dict[str, Any]:
        state.reload_suite()
        return {"ok": True, "name": state.suite.name, "task_count": len(state.suite.tasks)}

    @app.post("/api/estimate")
    def api_estimate(body: dict[str, Any]) -> dict[str, Any]:
        task_ids = body.get("task_ids") or [t.id for t in state.suite.tasks]
        seeds_override = body.get("seeds")
        model = body.get("model", "gemini-2.5-flash-lite")
        n_seed_runs = 0
        for tid in task_ids:
            try:
                task = state.suite.get(tid)
            except KeyError:
                continue
            n_seed_runs += len(seeds_override or task.seeds)
        tokens_in = n_seed_runs * _ESTIMATED_TOKENS_IN_PER_SEED
        tokens_out = n_seed_runs * _ESTIMATED_TOKENS_OUT_PER_SEED
        cost = _estimate_cost(model, tokens_in, tokens_out)
        return {
            "seed_runs": n_seed_runs,
            "estimated_tokens_in": tokens_in,
            "estimated_tokens_out": tokens_out,
            "estimated_cost": cost,
            "note": "Rough estimate from typical trajectory length; actual usage is measured per run.",
        }

    @app.post("/api/runs")
    def api_start_run(body: dict[str, Any]) -> dict[str, Any]:
        task_ids = body.get("task_ids") or [t.id for t in state.suite.tasks]
        for tid in task_ids:
            if tid not in {t.id for t in state.suite.tasks}:
                raise HTTPException(400, f"no such task: {tid}")
        backend_name = body.get("backend", "fake")
        if backend_name == "gemini" and not state.gemini_status()["ready"]:
            raise HTTPException(
                400,
                "Gemini backend is not ready. Install the 'gemini' extra, run "
                "`gcloud auth application-default login`, and set GOOGLE_CLOUD_PROJECT.",
            )
        job = state.start_run(
            task_ids=task_ids,
            seeds_override=body.get("seeds"),
            backend_name=backend_name,
            model=body.get("model", "gemini-2.5-flash-lite"),
            temperature=float(body.get("temperature", 0.0)),
            use_judge=bool(body.get("use_judge", True)),
        )
        return {"run_id": job.id}

    @app.get("/api/runs")
    def api_list_runs() -> dict[str, Any]:
        with state.runs_lock:
            order = list(state.run_order)
        return {"runs": [state.runs[rid].to_summary() for rid in reversed(order)]}

    @app.get("/api/runs/{run_id}")
    def api_get_run(run_id: str) -> dict[str, Any]:
        job = state.runs.get(run_id)
        if job is None:
            raise HTTPException(404, "no such run")
        return job.to_grid()

    @app.get("/api/runs/{run_id}/trajectory/{task_id}/{seed}")
    def api_get_trajectory(run_id: str, task_id: str, seed: str) -> dict[str, Any]:
        job = state.runs.get(run_id)
        if job is None:
            raise HTTPException(404, "no such run")
        row = job.tasks.get(task_id, {}).get(seed)
        if row is None:
            raise HTTPException(404, "no such task/seed in this run")
        return {"task_id": task_id, "seed": seed, **row}

    @app.get("/api/runs/{run_id}/stream")
    def api_stream_run(run_id: str) -> Any:
        job = state.runs.get(run_id)
        if job is None:
            raise HTTPException(404, "no such run")

        def gen() -> Iterator[str]:
            q: "queue.Queue[str]" = queue.Queue()
            with job.lock:
                job.listeners.append(q)
            yield f"data: {json.dumps({'type': 'snapshot', 'summary': job.to_summary()})}\n\n"
            try:
                while True:
                    try:
                        line = q.get(timeout=1.0)
                        yield f"data: {line}\n\n"
                        if '"type": "run_end"' in line or '"type":"run_end"' in line:
                            break
                    except queue.Empty:
                        if job.status != "running":
                            yield f"data: {json.dumps({'type': 'run_end', 'status': job.status, 'error': job.error})}\n\n"
                            break
                        yield ": keep-alive\n\n"
            finally:
                with job.lock:
                    if q in job.listeners:
                        job.listeners.remove(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/baseline")
    def api_save_baseline(run_id: str) -> dict[str, Any]:
        job = state.runs.get(run_id)
        if job is None:
            raise HTTPException(404, "no such run")
        reports = job.flakiness_reports()
        baseline = from_flakiness_reports(job.suite_name, reports)
        save_baseline(state.baseline_path, baseline)
        return {"ok": True, "task_count": len(baseline.tasks), "created_at": baseline.created_at}

    @app.get("/api/baseline")
    def api_get_baseline() -> dict[str, Any]:
        if not state.baseline_path.exists():
            return {"exists": False}
        baseline = load_baseline(state.baseline_path)
        return {
            "exists": True,
            "suite_name": baseline.suite_name,
            "created_at": baseline.created_at,
            "tasks": {tid: {"n": t.n, "passes": t.passes, "pass_rate": t.pass_rate, "flake_score": t.flake_score} for tid, t in baseline.tasks.items()},
        }

    @app.post("/api/gate")
    def api_gate(body: dict[str, Any]) -> dict[str, Any]:
        run_id = body.get("run_id")
        z = float(body.get("z", 1.96))
        job = state.runs.get(run_id) if run_id else None
        if job is None:
            raise HTTPException(404, "no such run")
        if not state.baseline_path.exists():
            raise HTTPException(400, "no baseline saved yet")
        baseline = load_baseline(state.baseline_path)
        reports = job.flakiness_reports()
        diffs = diff_against_baseline(baseline, reports, z=z)
        regressed = any(d.classification == "regressed" for d in diffs)
        return {
            "gate_passed": not regressed,
            "z": z,
            "diffs": [
                {
                    "task_id": d.task_id,
                    "classification": d.classification,
                    "baseline_pass_rate": d.baseline_pass_rate,
                    "current_pass_rate": d.current_pass_rate,
                    "margin": d.margin,
                    "delta": d.delta,
                }
                for d in diffs
            ],
        }

    @app.get("/api/calibration")
    def api_get_calibration() -> dict[str, Any]:
        rows = []
        for row in state.calibration_rows:
            r = dict(row)
            r["human_label"] = state.calibration_overrides.get(row["id"], row["human_label"])
            rows.append(r)
        return {"rows": rows}

    @app.post("/api/calibration/relabel")
    def api_relabel(body: dict[str, Any]) -> dict[str, Any]:
        row_id = body["id"]
        label = body["label"]
        if label not in ("pass", "fail"):
            raise HTTPException(400, "label must be 'pass' or 'fail'")
        with state.calibration_lock:
            state.calibration_overrides[row_id] = label
        return {"ok": True}

    @app.post("/api/calibration/reset")
    def api_reset_labels() -> dict[str, Any]:
        with state.calibration_lock:
            state.calibration_overrides.clear()
        return {"ok": True}

    @app.post("/api/calibration/run")
    def api_run_calibration(body: dict[str, Any]) -> dict[str, Any]:
        backend_name = body.get("backend", "fake")
        try:
            judge: JudgeBackend = GeminiJudge(model=body.get("model", "gemini-2.5-flash-lite")) if backend_name == "gemini" else OfflineRubricJudge()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, _friendly_backend_error(exc))

        rows = []
        for row in state.calibration_rows:
            r = dict(row)
            r["human_label"] = state.calibration_overrides.get(row["id"], row["human_label"])
            rows.append(r)

        try:
            result = run_calibration(judge, rows)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, _friendly_backend_error(exc))

        out = result.to_dict()
        if isinstance(judge, GeminiJudge):
            out["tokens_in"] = judge.tokens_in
            out["tokens_out"] = judge.tokens_out
            out["cost_estimate"] = _estimate_cost(
                body.get("model", "gemini-2.5-flash-lite"), judge.tokens_in, judge.tokens_out
            )
        return out

    @app.get("/api/cassettes")
    def api_list_cassettes() -> dict[str, Any]:
        files = sorted(state.cassette_dir.glob("*.jsonl"))
        items = []
        for f in files:
            name = f.stem
            if "-seed" in name:
                task_id, seed_part = name.rsplit("-seed", 1)
                try:
                    seed = int(seed_part)
                except ValueError:
                    task_id, seed = name, None
            else:
                task_id, seed = name, None
            items.append({"file": f.name, "task_id": task_id, "seed": seed, "size_bytes": f.stat().st_size})
        return {"cassette_dir": str(state.cassette_dir), "cassettes": items}

    @app.get("/api/cassettes/{file_name}")
    def api_get_cassette(file_name: str) -> dict[str, Any]:
        path = state.cassette_dir / file_name
        if not path.exists() or path.parent != state.cassette_dir:
            raise HTTPException(404, "no such cassette")
        from aeh.events import read_jsonl

        events = read_jsonl(str(path))
        return {"file": file_name, "events": [_event_to_dict(e) for e in events]}

    @app.post("/api/cassettes/{file_name}/replay")
    def api_replay_cassette(file_name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        path = state.cassette_dir / file_name
        if not path.exists():
            raise HTTPException(404, "no such cassette")
        name = path.stem
        if "-seed" not in name:
            raise HTTPException(400, "cassette filename doesn't follow <task_id>-seed<seed>.jsonl")
        task_id, seed_part = name.rsplit("-seed", 1)
        try:
            seed = int(seed_part)
            task = state.suite.get(task_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"can't resolve task/seed for replay: {exc}") from exc

        body = body or {}
        on_div = OnDivergence(body.get("on_divergence", "passthrough"))
        backend_name = body.get("backend", "gemini" if "gemini" in str(state.cassette_dir) else "fake")
        try:
            backend = state.make_backend(backend_name, body.get("model", "gemini-2.5-flash-lite"), 0.0)
            result = run_task(task, seed, mode="replay", cassette_path=str(path), on_divergence=on_div, backend=backend)
        except DivergenceError as exc:
            return {"ok": False, "diverged": True, "error": str(exc), "diff": exc.diff}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "diverged": False, "error": _friendly_backend_error(exc)}

        assertion_results = evaluate_assertions(result.final_state, result.tool_sequence, task.assertions)
        passed = result.error is None and all(a.passed for a in assertion_results)
        return {
            "ok": True,
            "diverged": result.diverged,
            "passed": passed,
            "final_state": result.final_state,
            "tool_sequence": result.tool_sequence,
            "assertions": [{"type": a.spec_type, "passed": a.passed, "message": a.message} for a in assertion_results],
            "events": [_event_to_dict(e) for e in result.events],
        }

    return app
