"""Shared fixtures for the aeh test suite."""

from __future__ import annotations

import pytest

from aeh.suite import Suite, Task


@pytest.fixture
def simple_task() -> Task:
    return Task(
        id="fix_retries",
        input="Set max_retries to 5",
        setup={"workspace": {"config.yaml": "max_retries: 2\ntimeout: 30\n"}},
        plan=[
            {"tool": "read_file", "args": {"path": "config.yaml"}},
            {"tool": "write_file", "args": {"path": "config.yaml", "content": "max_retries: 5\ntimeout: 30\n"}},
            {"finish": {"status": "done"}},
        ],
        assertions=[
            {"type": "file_contains", "path": "config.yaml", "text": "max_retries: 5"},
            {"type": "tool_sequence", "sequence": ["read_file", "write_file"], "mode": "exact"},
        ],
        rubric="Did the agent set max_retries to 5?",
        seeds=[1, 2, 3],
    )


@pytest.fixture
def flaky_task() -> Task:
    return Task(
        id="flaky_ticket",
        input="Resolve the ticket",
        setup={"workspace": {"ticket.md": "status: open\n"}},
        plan=[
            {"tool": "read_file", "args": {"path": "ticket.md"}},
            {"tool": "write_file", "args": {"path": "ticket.md", "content": "status: resolved\n"}},
            {"finish": {"status": "done"}},
        ],
        flaky={"step": 1, "seed_mod": 2, "args": {"path": "ticket.md", "content": "status: open\n"}},
        assertions=[{"type": "file_contains", "path": "ticket.md", "text": "status: resolved"}],
        rubric="Did the agent resolve the ticket?",
        seeds=[1, 2, 3, 4],
    )
