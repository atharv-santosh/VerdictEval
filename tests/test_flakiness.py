from aeh.flakiness import SeedOutcome, analyze, pass_at_k


def test_pass_at_k_all_pass_is_one():
    assert pass_at_k(5, 5, 1) == 1.0
    assert pass_at_k(5, 5, 5) == 1.0


def test_pass_at_k_all_fail_is_zero():
    assert pass_at_k(5, 0, 1) == 0.0


def test_pass_at_k_partial():
    assert abs(pass_at_k(5, 3, 1) - 0.6) < 1e-9


def test_stable_pass_and_stable_fail_have_zero_flake_score():
    outcomes = [SeedOutcome("a", s, True) for s in range(5)] + [SeedOutcome("b", s, False) for s in range(5)]
    reports = analyze(outcomes)
    by_id = {r.task_id: r for r in reports}
    assert by_id["a"].flake_score == 0.0
    assert by_id["a"].verdict == "stable-pass"
    assert by_id["b"].flake_score == 0.0
    assert by_id["b"].verdict == "stable-fail"


def test_flaky_task_has_positive_flake_score_and_is_ranked_first():
    outcomes = (
        [SeedOutcome("stable", s, True) for s in range(10)]
        + [SeedOutcome("flaky", s, s % 2 == 0) for s in range(10)]
    )
    reports = analyze(outcomes)
    assert reports[0].task_id == "flaky"
    assert reports[0].flake_score > 0
    assert reports[0].verdict == "flaky"


def test_flake_score_peaks_at_fifty_percent():
    outcomes_50 = [SeedOutcome("t", s, s % 2 == 0) for s in range(10)]
    outcomes_80 = [SeedOutcome("t", s, s % 10 < 8) for s in range(10)]
    r50 = analyze(outcomes_50)[0]
    r80 = analyze(outcomes_80)[0]
    assert r50.flake_score > r80.flake_score
