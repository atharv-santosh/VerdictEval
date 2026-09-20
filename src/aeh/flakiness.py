"""Flakiness analysis: pass@k, variance, and a flake score, per task, across seeds."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb


@dataclass(slots=True)
class SeedOutcome:
    """Whether one (task, seed) run passed."""

    task_id: str
    seed: int
    passed: bool


@dataclass(slots=True)
class TaskFlakinessReport:
    """Aggregated flakiness metrics for one task across all its seeds."""

    task_id: str
    n: int
    passes: int
    pass_rate: float
    variance: float
    flake_score: float
    pass_at_k: dict[int, float]

    @property
    def verdict(self) -> str:
        if self.passes == self.n:
            return "stable-pass"
        if self.passes == 0:
            return "stable-fail"
        return "flaky"


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021): P[>=1 pass in k draws of n, c passing]."""
    if k > n:
        raise ValueError(f"k={k} cannot exceed n={n}")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def analyze(outcomes: list[SeedOutcome]) -> list[TaskFlakinessReport]:
    """Group per-seed outcomes by task and compute flakiness metrics for each."""
    by_task: dict[str, list[SeedOutcome]] = {}
    for outcome in outcomes:
        by_task.setdefault(outcome.task_id, []).append(outcome)

    reports = []
    for task_id, task_outcomes in by_task.items():
        n = len(task_outcomes)
        c = sum(1 for o in task_outcomes if o.passed)
        pass_rate = c / n if n else 0.0
        variance = pass_rate * (1 - pass_rate)
        flake_score = 4.0 * variance
        pak = {k: pass_at_k(n, c, k) for k in range(1, n + 1)}
        reports.append(
            TaskFlakinessReport(
                task_id=task_id, n=n, passes=c, pass_rate=pass_rate, variance=variance,
                flake_score=flake_score, pass_at_k=pak,
            )
        )
    reports.sort(key=lambda r: r.flake_score, reverse=True)
    return reports
