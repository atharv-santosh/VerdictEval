"""Proves record -> replay is bit-deterministic: identical final state, tool sequence, and event stream for the same (task, seed)."""

from aeh.agent import run_task
from aeh.events import write_jsonl


def test_replay_matches_record_exactly(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")

    recorded = run_task(simple_task, seed=1, mode="record")
    write_jsonl(cassette, recorded.events)

    replayed = run_task(simple_task, seed=1, mode="replay", cassette_path=cassette)

    assert replayed.final_state == recorded.final_state
    assert replayed.tool_sequence == recorded.tool_sequence
    assert replayed.finished == recorded.finished
    assert replayed.error is None
    assert replayed.diverged is False
    assert len(replayed.events) == len(recorded.events)
    for rec_ev, rep_ev in zip(recorded.events, replayed.events):
        assert rec_ev.type == rep_ev.type
        if rec_ev.type in ("llm_call", "tool_call"):
            assert rec_ev.payload["request_hash"] == rep_ev.payload["request_hash"]
        if rec_ev.type in ("llm_result", "tool_result"):
            assert rec_ev.payload["response"] == rep_ev.payload["response"]


def test_replay_is_repeatable_across_multiple_runs(simple_task, tmp_path):
    cassette = str(tmp_path / "c.jsonl")
    recorded = run_task(simple_task, seed=2, mode="record")
    write_jsonl(cassette, recorded.events)

    first = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette)
    second = run_task(simple_task, seed=2, mode="replay", cassette_path=cassette)

    assert first.final_state == second.final_state == recorded.final_state
    assert first.tool_sequence == second.tool_sequence == recorded.tool_sequence


def test_flaky_task_seeds_produce_different_trajectories(flaky_task, tmp_path):
    outcomes = {}
    for seed in flaky_task.seeds:
        result = run_task(flaky_task, seed=seed, mode="record")
        outcomes[seed] = result.final_state["workspace"]["ticket.md"]
    assert outcomes[1] != outcomes[2]
    assert outcomes[1] == outcomes[3]
    assert outcomes[2] == outcomes[4]
