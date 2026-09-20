"""Trajectory recording and deterministic replay."""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from aeh.events import TrajectoryEvent, canonical_json, content_hash, now, read_jsonl, write_jsonl

_CALL_TO_RESULT: dict[str, str] = {
    "llm_call": "llm_result",
    "tool_call": "tool_result",
}


class OnDivergence(str, Enum):
    """Policy for how replay should react to a request-hash mismatch."""

    FAIL = "fail"
    RECORD = "record"
    PASSTHROUGH = "passthrough"


class DivergenceError(Exception):
    """Raised when a replayed request does not match what was recorded."""

    def __init__(self, kind: str, seq: int, expected: dict[str, Any], actual: dict[str, Any]) -> None:
        self.kind = kind
        self.seq = seq
        self.expected = expected
        self.actual = actual
        diff = "\n".join(
            difflib.unified_diff(
                canonical_json(expected).splitlines(),
                canonical_json(actual).splitlines(),
                fromfile="recorded_request",
                tofile="replayed_request",
                lineterm="",
            )
        )
        super().__init__(
            f"divergence at {kind} seq={seq}: replayed request does not match "
            f"recorded request\n{diff}"
        )
        self.diff = diff


@dataclass(slots=True)
class StepOutcome:
    """Result of one journaled call/result step, plus whether it diverged."""

    response: dict[str, Any]
    diverged: bool = False


class CassetteWriter:
    """Journals a live agent run to an in-memory event buffer, then to disk."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._seq = 0
        self.events: list[TrajectoryEvent] = []

    def _emit(self, type_: str, payload: dict[str, Any]) -> TrajectoryEvent:
        event = TrajectoryEvent(run_id=self.run_id, seq=self._seq, ts=now(), type=type_, payload=payload)
        self._seq += 1
        self.events.append(event)
        return event

    def run_start(self, payload: dict[str, Any]) -> None:
        self._emit("run_start", payload)

    def run_end(self, payload: dict[str, Any]) -> None:
        self._emit("run_end", payload)

    def error(self, payload: dict[str, Any]) -> None:
        self._emit("error", payload)

    def decision(self, payload: dict[str, Any]) -> None:
        self._emit("decision", payload)

    def step(self, kind: str, request: dict[str, Any], live_fn: Callable[[], dict[str, Any]]) -> StepOutcome:
        """Execute ``live_fn`` live and journal the call/result pair."""
        call_type = f"{kind}_call"
        result_type = f"{kind}_result"
        req_hash = content_hash(request)
        self._emit(call_type, {"request": request, "request_hash": req_hash})
        response = live_fn()
        self._emit(result_type, {"response": response})
        return StepOutcome(response=response, diverged=False)

    def write(self, path: str) -> None:
        write_jsonl(path, self.events)


class CassetteReader:
    """Replays a previously recorded cassette, detecting divergence."""

    def __init__(self, path: str, on_divergence: OnDivergence = OnDivergence.FAIL) -> None:
        self.path = path
        self.on_divergence = on_divergence
        self.events = read_jsonl(path)
        self._cursor = 0
        self.diverged_steps: list[int] = []
        self.rerecorded_events: list[TrajectoryEvent] = []
        self._next_seq = (max((e.seq for e in self.events), default=-1)) + 1

    @property
    def run_id(self) -> str:
        for event in self.events:
            if event.type == "run_start":
                return event.run_id
        raise ValueError("cassette has no run_start event")

    def _next_event(self, type_: str) -> TrajectoryEvent | None:
        while self._cursor < len(self.events):
            event = self.events[self._cursor]
            self._cursor += 1
            if event.type == type_:
                return event
            if event.type in ("run_start", "decision", "human_gate"):
                continue
        return None

    def step(self, kind: str, request: dict[str, Any], live_fn: Callable[[], dict[str, Any]]) -> StepOutcome:
        """Serve a call from the cassette, verifying the request hash."""
        call_type = f"{kind}_call"
        result_type = f"{kind}_result"
        call_event = self._next_event(call_type)
        if call_event is None:
            raise DivergenceError(kind, self._next_seq, {}, request)
        result_event = self._next_event(result_type)
        if result_event is None:
            raise DivergenceError(kind, call_event.seq, call_event.payload["request"], request)

        actual_hash = content_hash(request)
        expected_hash = call_event.payload["request_hash"]
        if actual_hash == expected_hash:
            return StepOutcome(response=result_event.payload["response"], diverged=False)

        self.diverged_steps.append(call_event.seq)
        if self.on_divergence == OnDivergence.FAIL:
            raise DivergenceError(kind, call_event.seq, call_event.payload["request"], request)
        if self.on_divergence == OnDivergence.PASSTHROUGH:
            return StepOutcome(response=result_event.payload["response"], diverged=True)
        response = live_fn()
        seq = self._next_seq
        self._next_seq += 1
        self.rerecorded_events.append(
            TrajectoryEvent(run_id=self.run_id, seq=seq, ts=now(), type=call_type, payload={"request": request, "request_hash": actual_hash})
        )
        seq2 = self._next_seq
        self._next_seq += 1
        self.rerecorded_events.append(
            TrajectoryEvent(run_id=self.run_id, seq=seq2, ts=now(), type=result_type, payload={"response": response})
        )
        return StepOutcome(response=response, diverged=True)
