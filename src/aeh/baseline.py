"""Baseline snapshots: the "known good" result set that a new run is diffed against."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aeh.flakiness import TaskFlakinessReport


@dataclass(slots=True)
class TaskBaseline:
    """Snapshot of one task's measured pass rate at baseline time."""

    task_id: str
    n: int
    passes: int
    pass_rate: float
    flake_score: float


@dataclass(slots=True)
class Baseline:
    """A named, timestamped set of per-task baselines."""

    suite_name: str
    created_at: float
    tasks: dict[str, TaskBaseline]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "created_at": self.created_at,
            "tasks": {
                tid: {"n": t.n, "passes": t.passes, "pass_rate": t.pass_rate, "flake_score": t.flake_score}
                for tid, t in self.tasks.items()
            },
        }


def from_flakiness_reports(suite_name: str, reports: list[TaskFlakinessReport]) -> Baseline:
    """Build a :class:`Baseline` from a completed flakiness analysis."""
    tasks = {
        r.task_id: TaskBaseline(task_id=r.task_id, n=r.n, passes=r.passes, pass_rate=r.pass_rate, flake_score=r.flake_score)
        for r in reports
    }
    return Baseline(suite_name=suite_name, created_at=time.time(), tasks=tasks)


def save_baseline(path: str | Path, baseline: Baseline) -> None:
    """Write a baseline snapshot to disk as JSON."""
    Path(path).write_text(json.dumps(baseline.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def load_baseline(path: str | Path) -> Baseline:
    """Read a baseline snapshot back from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = {tid: TaskBaseline(task_id=tid, **fields) for tid, fields in data["tasks"].items()}
    return Baseline(suite_name=data["suite_name"], created_at=data["created_at"], tasks=tasks)
