"""The trajectory event schema shared across the aeh, and its sibling, projects."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "run_start",
    "llm_call",
    "llm_result",
    "tool_call",
    "tool_result",
    "decision",
    "human_gate",
    "error",
    "run_end",
]

VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "run_start",
        "llm_call",
        "llm_result",
        "tool_call",
        "tool_result",
        "decision",
        "human_gate",
        "error",
        "run_end",
    }
)


@dataclass(slots=True)
class TrajectoryEvent:
    """One line of a recorded trajectory."""

    run_id: str
    seq: int
    ts: float
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid event type: {self.type!r}")

    def to_json(self) -> str:
        """Serialize to a single canonical JSON line (no trailing newline)."""
        return json.dumps(
            {
                "run_id": self.run_id,
                "seq": self.seq,
                "ts": self.ts,
                "type": self.type,
                "payload": self.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def from_json(line: str) -> "TrajectoryEvent":
        """Parse one JSONL line into a :class:`TrajectoryEvent`."""
        data = json.loads(line)
        return TrajectoryEvent(
            run_id=data["run_id"],
            seq=data["seq"],
            ts=data["ts"],
            type=data["type"],
            payload=data.get("payload", {}),
        )


def now() -> float:
    """Wall-clock timestamp used when stamping new events."""
    return time.time()


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used as the basis for content hashes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(obj: Any) -> str:
    """SHA-256 hex digest of the canonical JSON encoding of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def write_jsonl(path: str, events: list[TrajectoryEvent]) -> None:
    """Write a full list of events to ``path`` as JSONL, overwriting it."""
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.to_json())
            fh.write("\n")


def read_jsonl(path: str) -> list[TrajectoryEvent]:
    """Read a JSONL trajectory file back into a list of events, in order."""
    events: list[TrajectoryEvent] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(TrajectoryEvent.from_json(line))
    return events
