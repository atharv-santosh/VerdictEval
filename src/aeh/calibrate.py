"""Judge calibration: measure judge-vs-human agreement on labeled trajectories."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aeh.scoring.judge import JudgeBackend


@dataclass(slots=True)
class CalibrationResult:
    """Judge-vs-human agreement metrics over a labeled trajectory set."""

    n: int
    raw_agreement: float
    kappa: float
    confusion: dict[str, dict[str, int]]
    disagreements: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "raw_agreement": self.raw_agreement,
            "kappa": self.kappa,
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


def load_calibration_set(path: str | Path) -> list[dict[str, Any]]:
    """Load the human-labeled trajectory fixtures used for calibration."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def calibrate(judge: JudgeBackend, rows: list[dict[str, Any]]) -> CalibrationResult:
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
                    "human_label": human_label,
                    "judge_label": judge_label,
                    "rationale": verdict.rationale,
                }
            )

    n = len(rows)
    raw_agreement = sum(1 for a, b in pairs if a == b) / n if n else 0.0
    kappa = cohens_kappa(pairs)
    return CalibrationResult(
        n=n, raw_agreement=raw_agreement, kappa=kappa, confusion=confusion, disagreements=disagreements
    )
