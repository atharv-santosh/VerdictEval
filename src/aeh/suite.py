"""Declarative task suites: load YAML task definitions into :class:`Task` objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class Task:
    """One task in a suite."""

    id: str
    input: str
    setup: dict[str, Any] = field(default_factory=dict)
    plan: list[dict[str, Any]] = field(default_factory=list)
    flaky: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    rubric: str = ""
    seeds: list[int] = field(default_factory=lambda: [0])
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Suite:
    """A named collection of tasks loaded from a YAML file."""

    name: str
    tasks: list[Task]

    def get(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"no such task: {task_id!r}")


def load_suite(path: str | Path) -> Suite:
    """Parse a suite YAML file into a :class:`Suite`."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    tasks = [
        Task(
            id=t["id"],
            input=t["input"],
            setup=t.get("setup", {}),
            plan=t.get("plan", []),
            flaky=t.get("flaky"),
            assertions=t.get("assertions", []),
            rubric=t.get("rubric", ""),
            seeds=t.get("seeds", [0]),
            tags=t.get("tags", []),
        )
        for t in data["tasks"]
    ]
    return Suite(name=data.get("name", Path(path).stem), tasks=tasks)
