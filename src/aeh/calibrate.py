"""Judge calibration: measure judge-vs-human agreement on labeled trajectories."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aeh.metrics import percentile
from aeh.scoring.judge import JudgeBackend

DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_SEED = 20260925


@dataclass(slots=True)
class CalibrationResult:
    """Judge-vs-human agreement metrics over a labeled trajectory set."""

    n: int
    raw_agreement: float
    kappa: float
    kappa_ci_low: float
    kappa_ci_high: float
    confusion: dict[str, dict[str, int]]
    disagreements: list[dict[str, Any]]
    split: str = "all"

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "split": self.split,
            "raw_agreement": self.raw_agreement,
            "kappa": self.kappa,
            "kappa_ci_low": self.kappa_ci_low,
            "kappa_ci_high": self.kappa_ci_high,
            "confusion": self.confusion,
            "disagreements": self.disagreements,
        }


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """Compute Cohen's kappa for a list of (rater_a_label, rater_b_label) pairs."""
    n = len(pairs)
    if n == 0:
        return 0.0

    labels = sorted({label for pair in pairs for label in pair})
    a_counts = {label: 0 for label in labels}
    b_counts = {label: 0 for label in labels}
    agree = 0
    for a, b in pairs:
        a_counts[a] += 1
        b_counts[b] += 1
        if a == b:
            agree += 1

    p_observed = agree / n
    p_expected = sum((a_counts[label] / n) * (b_counts[label] / n) for label in labels)

    if p_expected >= 1.0:
        return 1.0
    return (p_observed - p_expected) / (1 - p_expected)


def bootstrap_kappa_ci(
    pairs: list[tuple[str, str]],
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    alpha: float = 0.05,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for Cohen's kappa.

    Resamples the (human, judge) pairs with replacement ``resamples`` times and
    returns the empirical ``alpha/2`` and ``1 - alpha/2`` quantiles of kappa.
    Resamples in which every pair carries the same label give an undefined kappa
    and are dropped rather than counted as agreement.
    """
    n = len(pairs)
    if n < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        labels = {label for pair in sample for label in pair}
        if len(labels) < 2:
            continue
        draws.append(cohens_kappa(sample))
    if not draws:
        return (0.0, 0.0)
    return (percentile(draws, 100 * alpha / 2), percentile(draws, 100 * (1 - alpha / 2)))


def load_calibration_set(path: str | Path, split: str = "all") -> list[dict[str, Any]]:
    """Load the human-labeled trajectory fixtures, optionally one split only."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if split != "all" and row.get("split", "all") != split:
                continue
            rows.append(row)
    return rows


def calibrate(
    judge: JudgeBackend,
    rows: list[dict[str, Any]],
    split: str = "all",
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> CalibrationResult:
    """Score every labeled trajectory with ``judge`` and compare to human labels."""
    confusion: dict[str, dict[str, int]] = {}
    disagreements: list[dict[str, Any]] = []
    pairs: list[tuple[str, str]] = []

    for row in rows:
        human_label = row["human_label"]
        verdict = judge.score(row["rubric_hint"], row["final_state"])
        judge_label = verdict.label
        pairs.append((human_label, judge_label))
        confusion.setdefault(human_label, {}).setdefault(judge_label, 0)
        confusion[human_label][judge_label] += 1
        if human_label != judge_label:
            disagreements.append(
                {
                    "id": row.get("id"),
                    "split": row.get("split"),
                    "human_label": human_label,
                    "judge_label": judge_label,
                    "rationale": verdict.rationale,
                    "note": row.get("note"),
                }
            )

    n = len(rows)
    raw_agreement = sum(1 for a, b in pairs if a == b) / n if n else 0.0
    kappa = cohens_kappa(pairs)
    ci_low, ci_high = bootstrap_kappa_ci(pairs, resamples=resamples)
    return CalibrationResult(
        n=n,
        raw_agreement=raw_agreement,
        kappa=kappa,
        kappa_ci_low=ci_low,
        kappa_ci_high=ci_high,
        confusion=confusion,
        disagreements=disagreements,
        split=split,
    )
