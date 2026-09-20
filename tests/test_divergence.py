"""Proves divergence detection fires when replay takes a different path than what was recorded, and that all three --on-divergence policies behave as documented."""

from aeh.agent import run_task
from aeh.cassette import OnDivergence
from aeh.events import write_jsonl


def test_replay_with_different_seed_diverges(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette, on_divergence=OnDivergence.FAIL)

    assert replayed.error is not None
    assert "divergence" in replayed.error.lower()


def test_divergence_error_diff_names_both_seeds(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette, on_divergence=OnDivergence.FAIL)

    assert '"seed":1' in replayed.error
    assert '"seed":2' in replayed.error


def test_on_divergence_passthrough_serves_stale_response_and_flags_diverged(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette, on_divergence=OnDivergence.PASSTHROUGH)

    assert replayed.error is None
    assert replayed.diverged is True
    assert replayed.final_state == recorded.final_state


def test_on_divergence_record_falls_back_to_live_and_still_completes(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette, on_divergence=OnDivergence.RECORD)

    assert replayed.error is None
    assert replayed.diverged is True
    assert replayed.final_state["workspace"]["config.yaml"] == "max_retries: 5\ntimeout: 30\n"


def test_no_divergence_when_seed_matches(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=1, mode="replay", cassette_path=cassette, on_divergence=OnDivergence.FAIL)

    assert replayed.error is None
    assert replayed.diverged is False
