from aeh.baseline import Baseline, TaskBaseline
from aeh.flakiness import TaskFlakinessReport
from aeh.gate import diff_against_baseline, has_regression, standard_error


def _report(task_id, n, passes):
    pass_rate = passes / n
    variance = pass_rate * (1 - pass_rate)
    return TaskFlakinessReport(task_id, n, passes, pass_rate, variance, 4 * variance, {})


def test_standard_error_zero_when_deterministic():
    assert standard_error(1.0, 5) == 0.0
    assert standard_error(0.0, 5) == 0.0


def test_stable_task_regression_is_never_tolerated():
    baseline = Baseline("s", 0.0, {"t": TaskBaseline("t", 5, 5, 1.0, 0.0)})
    current = [_report("t", 5, 4)]
    diffs = diff_against_baseline(baseline, current)
    assert diffs[0].classification == "regressed"
    assert has_regression(diffs)


def test_flaky_task_normal_variance_is_not_flagged():
    baseline = Baseline("s", 0.0, {"t": TaskBaseline("t", 5, 3, 0.6, 0.96)})
    current = [_report("t", 5, 2)]
    diffs = diff_against_baseline(baseline, current)
    assert diffs[0].classification == "unchanged"
    assert not has_regression(diffs)


def test_improvement_is_classified_as_improved():
    baseline = Baseline("s", 0.0, {"t": TaskBaseline("t", 5, 2, 0.4, 0.96)})
    current = [_report("t", 5, 5)]
    diffs = diff_against_baseline(baseline, current)
    assert diffs[0].classification == "improved"


def test_new_task_not_in_baseline():
    baseline = Baseline("s", 0.0, {})
    current = [_report("new_task", 5, 5)]
    diffs = diff_against_baseline(baseline, current)
    assert diffs[0].classification == "new"


def test_removed_task_not_in_current():
    baseline = Baseline("s", 0.0, {"gone": TaskBaseline("gone", 5, 5, 1.0, 0.0)})
    diffs = diff_against_baseline(baseline, [])
    assert diffs[0].classification == "removed"
