"""Token, dollar and wall-clock rollups."""

from __future__ import annotations

from aeh.agent import run_task
from aeh.events import token_totals, wall_clock_seconds
from aeh.metrics import cost_per_solved, estimate_cost, fmt_cost, percentile, summarize
from aeh.suite import load_suite


def test_percentile_interpolates():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 2.5
    assert percentile(values, 100) == 4.0


def test_percentile_edge_cases():
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 95) == 7.0


def test_estimate_cost_uses_published_rates():
    assert estimate_cost("gemini-2.5-flash-lite", 1_000_000, 0) == 0.10
    assert estimate_cost("gemini-2.5-flash-lite", 0, 1_000_000) == 0.40
    assert estimate_cost("no-such-model", 1_000_000, 1_000_000) is None


def test_cost_per_solved_needs_a_solved_task():
    assert cost_per_solved(0.004, 20) == 0.0002
    assert cost_per_solved(0.004, 0) is None
    assert cost_per_solved(None, 5) is None


def test_fmt_cost_keeps_small_figures_visible():
    assert fmt_cost(None) == "-"
    assert fmt_cost(0.0) == "$0"
    assert fmt_cost(0.000158) == "$0.000158"
    assert fmt_cost(0.0041) == "$0.0041"


def test_summarize_rolls_up_seed_rows():
    rows = [
        {"tokens_in": 100, "tokens_out": 10, "wall_clock_s": 1.0},
        {"tokens_in": 200, "tokens_out": 20, "wall_clock_s": 3.0},
    ]
    cl = summarize(rows, "gemini-2.5-flash-lite")
    assert (cl.n, cl.tokens_in, cl.tokens_out) == (2, 300, 30)
    assert cl.p50_s == 2.0
    assert cl.cost_usd is not None and cl.cost_usd > 0


def test_summarize_is_free_when_unpriced():
    cl = summarize([{"tokens_in": 10, "tokens_out": 1, "wall_clock_s": 0.5}], "", priced=False)
    assert cl.cost_usd == 0.0


def test_replayed_cassette_reports_recorded_latency_not_replay_time():
    """Replay reads the cassette's own timestamps, so the reported wall clock is the live model's latency, not the cost of parsing a file."""
    task = load_suite("demo/suite.yaml").get("fix_max_retries")
    path = "cassettes/gemini-flakiness/fix_max_retries-seed1.jsonl"
    result = run_task(task, 1, mode="replay", cassette_path=path)

    assert result.tokens_in > 0 and result.tokens_out > 0
    assert result.wall_clock_s > 0.5
    assert (result.tokens_in, result.tokens_out) == token_totals(result.events)
    assert result.wall_clock_s == wall_clock_seconds(result.events)
