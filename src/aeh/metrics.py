"""Token, dollar and wall-clock rollups shared by the CLI, the reports and the web app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

FAKE_MODEL_LABEL = "fake-backend (offline, no model called)"

GEMINI_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Dollar cost at the model's published per-million-token rates, or None if the model is unpriced."""
    prices = GEMINI_PRICE_PER_MTOK.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    return (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile of ``values`` at ``q`` in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (q / 100.0) * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


@dataclass(slots=True)
class CostLatency:
    """Tokens, dollars and wall-clock percentiles over a set of seed runs."""

    n: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    p50_s: float
    p95_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "p50_s": self.p50_s,
            "p95_s": self.p95_s,
        }


def summarize(rows: Iterable[dict[str, Any]], model: str, priced: bool = True) -> CostLatency:
    """Roll up per-seed rows carrying ``tokens_in``/``tokens_out``/``wall_clock_s``."""
    rows = list(rows)
    tokens_in = sum(int(r.get("tokens_in", 0) or 0) for r in rows)
    tokens_out = sum(int(r.get("tokens_out", 0) or 0) for r in rows)
    latencies = [float(r.get("wall_clock_s", 0.0) or 0.0) for r in rows]
    cost = estimate_cost(model, tokens_in, tokens_out) if priced else 0.0
    return CostLatency(
        n=len(rows),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        p50_s=percentile(latencies, 50),
        p95_s=percentile(latencies, 95),
    )


def cost_per_solved(cost_usd: float | None, solved: int) -> float | None:
    """Dollars per passing seed run. None when cost is unknown; None when nothing passed."""
    if cost_usd is None or solved <= 0:
        return None
    return cost_usd / solved


def fmt_cost(cost: float | None) -> str:
    """Format a dollar figure for a fixed-width terminal column."""
    if cost is None:
        return "-"
    if cost == 0:
        return "$0"
    return f"${cost:.6f}" if abs(cost) < 0.001 else f"${cost:.4f}"
