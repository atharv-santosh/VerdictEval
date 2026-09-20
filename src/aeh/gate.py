"""Regression gate: diff a new run against a baseline and exit non-zero on regression."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from aeh.baseline import Baseline
from aeh.flakiness import TaskFlakinessReport

Classification = Literal["improved", "regressed", "unchanged", "new", "removed"]


@dataclass(slots=True)
class TaskDiff:
    """Baseline-vs-current comparison for one task."""

    task_id: str
    classification: Classification
    baseline_pass_rate: float | None
    current_pass_rate: float | None
    margin: float
    delta: float | None


def standard_error(pass_rate: float, n: int) -> float:
    """Standard error of a Bernoulli pass-rate estimate over ``n`` seeds."""
    if n <= 0:
        return 0.0
    return math.sqrt(pass_rate * (1 - pass_rate) / n)


def diff_against_baseline(
    baseline: Baseline, current: list[TaskFlakinessReport], z: float = 1.96
) -> list[TaskDiff]:
    """Classify every task as improved/regressed/unchanged/new/removed."""
    current_by_id = {r.task_id: r for r in current}
    diffs: list[TaskDiff] = []

    for task_id, base in baseline.tasks.items():
        cur = current_by_id.pop(task_id, None)
        if cur is None:
            diffs.append(TaskDiff(task_id, "removed", base.pass_rate, None, 0.0, None))
            continue
        margin = z * standard_error(base.pass_rate, base.n)
        delta = cur.pass_rate - base.pass_rate
        if delta < -margin:
            classification: Classification = "regressed"
        elif delta > margin:
            classification = "improved"
        else:
            classification = "unchanged"
        diffs.append(TaskDiff(task_id, classification, base.pass_rate, cur.pass_rate, margin, delta))

    for task_id, cur in current_by_id.items():
        diffs.append(TaskDiff(task_id, "new", None, cur.pass_rate, 0.0, None))

    return diffs


def has_regression(diffs: list[TaskDiff]) -> bool:
    """True if any task in the diff was classified as regressed."""
    return any(d.classification == "regressed" for d in diffs)
