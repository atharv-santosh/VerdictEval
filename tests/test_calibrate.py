from aeh.calibrate import bootstrap_kappa_ci, calibrate, cohens_kappa, load_calibration_set
from aeh.scoring.judge import OfflineRubricJudge


def test_kappa_perfect_agreement_is_one():
    pairs = [("pass", "pass"), ("fail", "fail"), ("pass", "pass"), ("fail", "fail")]
    assert cohens_kappa(pairs) == 1.0


def test_kappa_below_chance_is_negative():
    pairs = [("pass", "fail"), ("fail", "pass"), ("pass", "fail"), ("fail", "pass")]
    assert cohens_kappa(pairs) < 0


def test_kappa_empty_is_zero():
    assert cohens_kappa([]) == 0.0


def test_calibrate_on_shipped_dataset_produces_sane_metrics():
    rows = load_calibration_set("data/calibration_set.jsonl")
    assert len(rows) >= 15
    judge = OfflineRubricJudge()
    result = calibrate(judge, rows)
    assert result.n == len(rows)
    assert 0.0 <= result.raw_agreement <= 1.0
    assert -1.0 <= result.kappa <= 1.0
    assert result.raw_agreement < 1.0
    assert len(result.disagreements) > 0


def test_bootstrap_ci_brackets_the_point_estimate():
    pairs = [("pass", "pass")] * 8 + [("fail", "fail")] * 8 + [("pass", "fail")] * 4
    kappa = cohens_kappa(pairs)
    low, high = bootstrap_kappa_ci(pairs, resamples=500)
    assert low <= kappa <= high
    assert -1.0 <= low <= high <= 1.0


def test_bootstrap_ci_is_deterministic_for_a_fixed_seed():
    pairs = [("pass", "pass")] * 6 + [("fail", "pass")] * 3 + [("fail", "fail")] * 6
    assert bootstrap_kappa_ci(pairs, resamples=300) == bootstrap_kappa_ci(pairs, resamples=300)


def test_bootstrap_ci_is_wider_on_less_data():
    small = [("pass", "pass")] * 4 + [("fail", "fail")] * 4 + [("pass", "fail")]
    large = small * 8
    small_low, small_high = bootstrap_kappa_ci(small, resamples=800)
    large_low, large_high = bootstrap_kappa_ci(large, resamples=800)
    assert (small_high - small_low) > (large_high - large_low)


def test_bootstrap_ci_on_degenerate_input_is_zero():
    assert bootstrap_kappa_ci([("pass", "pass")]) == (0.0, 0.0)


def test_splits_partition_the_shipped_set():
    everything = load_calibration_set("data/calibration_set.jsonl")
    tune = load_calibration_set("data/calibration_set.jsonl", split="tune")
    holdout = load_calibration_set("data/calibration_set.jsonl", split="holdout")
    assert len(everything) >= 60
    assert len(tune) + len(holdout) == len(everything)
    assert not ({r["id"] for r in tune} & {r["id"] for r in holdout})
    for rows in (tune, holdout):
        labels = [r["human_label"] for r in rows]
        assert labels.count("pass") >= 10 and labels.count("fail") >= 10


def test_calibration_result_carries_an_interval():
    rows = load_calibration_set("data/calibration_set.jsonl", split="holdout")
    result = calibrate(OfflineRubricJudge(), rows, split="holdout", resamples=300)
    assert result.split == "holdout"
    assert result.kappa_ci_low <= result.kappa <= result.kappa_ci_high
    assert result.to_dict()["kappa_ci_high"] == result.kappa_ci_high
