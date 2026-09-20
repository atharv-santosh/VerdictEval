"""The demo agent's run loop: drives the fake backend and the four workspace tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aeh.backends.base import ModelBackend
from aeh.backends.fake import FakeModelBackend
from aeh.cassette import CassetteReader, CassetteWriter, DivergenceError, OnDivergence
from aeh.suite import Task
from aeh.workspace import TOOL_REGISTRY, TOOL_SPECS, Workspace

_MUTATING_TOOLS = {"write_file"}

FINISH_SPEC: dict[str, Any] = {
    "name": "finish",
    "description": "Call this exactly once, when (and only when) the task is complete.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "e.g. 'done'"},
            "message": {"type": "string", "description": "Optional short summary of what was done."},
        },
        "required": ["status"],
    },
}

DEFAULT_MAX_STEPS = 20


@dataclass(slots=True)
class RunResult:
    """Outcome of one task/seed run, whether recorded or replayed."""

    run_id: str
    task_id: str
    seed: int
    final_state: dict[str, Any]
    tool_sequence: list[str]
    finished: bool
    flake_triggered: bool
    diverged: bool
    error: str | None = None
    events: list[Any] = field(default_factory=list)


def _apply_tool_result(ws: Workspace, tool_name: str, args: dict[str, Any], response: dict[str, Any]) -> None:
    """Mutate the workspace from a tool's *response*, not by recomputing it."""
    if tool_name == "write_file" and "content" in response:
        ws.files[response["path"]] = response["content"]


def run_task(
    task: Task,
    seed: int,
    mode: str,
    cassette_path: str | None = None,
    on_divergence: OnDivergence = OnDivergence.FAIL,
    backend: ModelBackend | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> RunResult:
    """Run one task at one seed, either recording or replaying a cassette."""
    if mode not in ("record", "replay"):
        raise ValueError(f"unknown mode: {mode!r}")

    ws = Workspace(files=dict(task.setup.get("workspace", {})))
    backend = backend or FakeModelBackend()
    run_id = f"{task.id}-seed{seed}"

    journal: CassetteWriter | CassetteReader
    if mode == "record":
        journal = CassetteWriter(run_id=run_id)
        journal.run_start({"task_id": task.id, "seed": seed, "input": task.input})
    else:
        if not cassette_path:
            raise ValueError("cassette_path is required for replay mode")
        journal = CassetteReader(cassette_path, on_divergence=on_divergence)

    tool_sequence: list[str] = []
    final_state: dict[str, Any] = {}
    finished = False
    flake_triggered = False
    diverged = False
    error: str | None = None
    history: list[dict[str, Any]] = []

    try:
        for step_index in range(max_steps):
            llm_request = {
                "task_id": task.id,
                "seed": seed,
                "step_index": step_index,
                "plan": task.plan,
                "flaky": task.flaky,
                "input": task.input,
                "history": history,
                "tools": TOOL_SPECS + [FINISH_SPEC],
            }
            def live_llm(r: dict[str, Any] = llm_request) -> dict[str, Any]:
                return backend.generate(r)

            llm_outcome = journal.step("llm", llm_request, live_fn=live_llm)
            diverged = diverged or llm_outcome.diverged
            response = llm_outcome.response
            flake_triggered = flake_triggered or bool(response.get("flake_triggered"))
            action = response["action"]

            if "finish" in action:
                final_state = {"workspace": ws.snapshot(), **action["finish"]}
                finished = True
                break

            tool_name = action["tool"]
            tool_args = action["args"]
            tool_sequence.append(tool_name)
            tool_request = {"tool": tool_name, "args": tool_args}

            def live_tool(name: str = tool_name, args: dict[str, Any] = tool_args) -> dict[str, Any]:
                return TOOL_REGISTRY[name](ws, **args)

            tool_outcome = journal.step("tool", tool_request, live_fn=live_tool)
            diverged = diverged or tool_outcome.diverged
            _apply_tool_result(ws, tool_name, tool_args, tool_outcome.response)
            history.append({"tool": tool_name, "args": tool_args, "response": tool_outcome.response})
        else:
            final_state = {"workspace": ws.snapshot(), "status": "incomplete"}
    except DivergenceError as exc:
        error = str(exc)
        final_state = {"workspace": ws.snapshot(), "status": "divergence_error"}
    except Exception as exc:  # noqa: BLE001 - surfaced in the report, not swallowed
        error = f"{type(exc).__name__}: {exc}"
        final_state = {"workspace": ws.snapshot(), "status": "runtime_error"}

    if isinstance(journal, CassetteWriter):
        journal.run_end({"final_state": final_state, "finished": finished, "error": error})
        events = journal.events
    else:
        events = journal.events + journal.rerecorded_events

    return RunResult(
        run_id=run_id,
        task_id=task.id,
        seed=seed,
        final_state=final_state,
        tool_sequence=tool_sequence,
        finished=finished,
        flake_triggered=flake_triggered,
        diverged=diverged,
        error=error,
        events=events,
    )
