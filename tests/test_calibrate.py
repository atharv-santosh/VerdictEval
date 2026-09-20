from aeh.calibrate import calibrate, cohens_kappa, load_calibration_set
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
