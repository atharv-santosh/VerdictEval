"""Real-Gemini backend tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aeh.agent import run_task
from aeh.backends.gemini import GeminiBackend
from aeh.scoring.assertions import evaluate_assertions
from aeh.suite import load_suite

REPO_ROOT = Path(__file__).resolve().parent.parent
GEMINI_CASSETTE_DIR = REPO_ROOT / "cassettes" / "gemini-flakiness"
DEMO_SUITE = REPO_ROOT / "demo" / "suite.yaml"

_HAS_LIVE_CREDS = bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))


def test_gemini_backend_constructs_without_any_credentials(monkeypatch):
    """Construction must never touch the network or check credentials -- that is what makes it safe to pass into a pure cassette replay."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    backend = GeminiBackend()
    assert backend.location == "global"
    assert backend.project is None


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_replays_every_real_gemini_cassette_with_zero_credentials(monkeypatch, seed):
    """The committed real-Gemini cassettes replay deterministically with no Google Cloud credentials and no network access at all."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    suite = load_suite(DEMO_SUITE)
    backend = GeminiBackend()
    for task in suite.tasks:
        cassette_path = GEMINI_CASSETTE_DIR / f"{task.id}-seed{seed}.jsonl"
        assert cassette_path.exists(), f"missing committed cassette: {cassette_path}"
        result = run_task(task, seed, mode="replay", cassette_path=str(cassette_path), backend=backend)
        assert result.error is None, f"{task.id} seed={seed}: {result.error}"
        assert result.diverged is False


def test_replaying_real_gemini_cassettes_is_repeatable():
    """Replaying the same real-Gemini cassette twice gives byte-identical final state -- record/replay determinism holds for real-model trajectories exactly as it does for the fake backend."""
    suite = load_suite(DEMO_SUITE)
    task = suite.get("fix_max_retries")
    backend = GeminiBackend()
    path = str(GEMINI_CASSETTE_DIR / "fix_max_retries-seed1.jsonl")

    first = run_task(task, 1, mode="replay", cassette_path=path, backend=backend)
    second = run_task(task, 1, mode="replay", cassette_path=path, backend=backend)

    assert first.final_state == second.final_state
    assert first.tool_sequence == second.tool_sequence


def test_committed_gemini_cassettes_are_real_model_trajectories_not_fake_plans():
    """Sanity check that these cassettes really did come from a real model:"""
    suite = load_suite(DEMO_SUITE)
    task = suite.get("fix_max_retries")
    backend = GeminiBackend()
    path = str(GEMINI_CASSETTE_DIR / "fix_max_retries-seed1.jsonl")
    result = run_task(task, 1, mode="replay", cassette_path=path, backend=backend)
    scripted = [step["tool"] for step in task.plan if "tool" in step]
    assert result.tool_sequence != scripted


@pytest.mark.skipif(not _HAS_LIVE_CREDS, reason="GOOGLE_CLOUD_PROJECT not set -- live Gemini test skipped, not failed")
def test_live_gemini_smoke_call():
    """Opt-in live test: only runs when GOOGLE_CLOUD_PROJECT is set (see README.md for setup)."""
    suite = load_suite(DEMO_SUITE)
    task = suite.get("fix_max_retries")
    backend = GeminiBackend(temperature=0.0)
    result = run_task(task, seed=999, mode="record", backend=backend)
    assert result.error is None
    assertion_results = evaluate_assertions(
        result.final_state, result.tool_sequence, [a for a in task.assertions if a["type"] != "tool_sequence"]
    )
    assert all(a.passed for a in assertion_results), assertion_results
