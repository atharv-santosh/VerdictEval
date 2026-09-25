"""Programmatic scorers: final-state checks, tool-path matchers, forbidden actions.

Tool-path assertions come in three strengths. ``tool_sequence`` with ``mode:
exact`` pins the whole path and breaks whenever a model explores. ``mode:
subsequence`` requires the named calls in order but tolerates extra calls around
them. ``required_tools`` drops order entirely and asks only that each named tool
was used. The weaker two express intent; the exact mode expresses a script.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AssertionResult:
    """Outcome of evaluating one assertion spec against a run."""

    spec_type: str
    passed: bool
    message: str


def _file_contains(final_state: dict[str, Any], spec: dict[str, Any]) -> AssertionResult:
    workspace = final_state.get("workspace", {})
    path, text = spec["path"], spec["text"]
    content = workspace.get(path, "")
    passed = text in content
    msg = f"{path!r} {'contains' if passed else 'MISSING'} {text!r}"
    return AssertionResult("file_contains", passed, msg)


def _file_equals(final_state: dict[str, Any], spec: dict[str, Any]) -> AssertionResult:
    workspace = final_state.get("workspace", {})
    path, expected = spec["path"], spec["content"]
    actual = workspace.get(path)
    passed = actual == expected
    msg = f"{path!r} {'matches' if passed else f'MISMATCH: got {actual!r}, want {expected!r}'}"
    return AssertionResult("file_equals", passed, msg)


TOOL_SEQUENCE_MODES = ("exact", "subsequence", "set")


def _tool_sequence(tool_sequence: list[str], spec: dict[str, Any]) -> AssertionResult:
    expected = spec["sequence"]
    mode = spec.get("mode", "exact")
    if mode == "exact":
        passed = tool_sequence == expected
    elif mode == "subsequence":
        it = iter(tool_sequence)
        passed = all(any(x == want for x in it) for want in expected)
    elif mode == "set":
        passed = set(expected).issubset(set(tool_sequence))
    else:
        raise ValueError(f"unknown tool_sequence mode: {mode!r}")
    msg = f"tool sequence {tool_sequence} vs expected {expected} ({mode}): {'ok' if passed else 'FAIL'}"
    return AssertionResult("tool_sequence", passed, msg)


def _required_tools(tool_sequence: list[str], spec: dict[str, Any]) -> AssertionResult:
    """Order-free set containment: every named tool was called at least once, in any order, with any amount of exploration around it."""
    required = set(spec["tools"])
    called = set(tool_sequence)
    missing = sorted(required - called)
    passed = not missing
    msg = (
        f"required tools {sorted(required)} all called"
        if passed
        else f"required tools {sorted(required)} NOT all called; missing {missing} (called {tool_sequence})"
    )
    return AssertionResult("required_tools", passed, msg)


def _forbidden_tool(tool_sequence: list[str], spec: dict[str, Any]) -> AssertionResult:
    tool = spec["tool"]
    passed = tool not in tool_sequence
    msg = f"forbidden tool {tool!r} {'not called' if passed else 'WAS CALLED'}"
    return AssertionResult("forbidden_tool", passed, msg)


def _status_equals(final_state: dict[str, Any], spec: dict[str, Any]) -> AssertionResult:
    expected = spec["status"]
    actual = final_state.get("status")
    passed = actual == expected
    msg = f"status {actual!r} {'==' if passed else '!='} expected {expected!r}"
    return AssertionResult("status_equals", passed, msg)


_HANDLERS = {
    "file_contains": lambda fs, ts, spec: _file_contains(fs, spec),
    "file_equals": lambda fs, ts, spec: _file_equals(fs, spec),
    "tool_sequence": lambda fs, ts, spec: _tool_sequence(ts, spec),
    "required_tools": lambda fs, ts, spec: _required_tools(ts, spec),
    "forbidden_tool": lambda fs, ts, spec: _forbidden_tool(ts, spec),
    "status_equals": lambda fs, ts, spec: _status_equals(fs, spec),
}


ASSERTION_TYPES: tuple[str, ...] = tuple(sorted(_HANDLERS))


def evaluate_assertions(
    final_state: dict[str, Any], tool_sequence: list[str], specs: list[dict[str, Any]]
) -> list[AssertionResult]:
    """Evaluate every assertion spec in ``specs`` against one run's outcome."""
    results = []
    for spec in specs:
        handler = _HANDLERS.get(spec["type"])
        if handler is None:
            raise ValueError(f"unknown assertion type: {spec['type']!r}")
        results.append(handler(final_state, tool_sequence, spec))
    return results
