"""Backend protocol shared by fake and real model backends."""

from __future__ import annotations

from typing import Any, Protocol


class ModelBackend(Protocol):
    """Anything that can turn a step request into an agent action."""

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return ``{"action": {...}, "tokens_in": int, "tokens_out": int}``."""
        ...
