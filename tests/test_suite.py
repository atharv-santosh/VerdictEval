from aeh.suite import load_suite


def test_demo_suite_loads_and_has_expected_tasks():
    suite = load_suite("demo/suite.yaml")
    assert suite.name == "demo-suite"
    assert len(suite.tasks) >= 8
    ids = {t.id for t in suite.tasks}
    assert "fix_max_retries" in ids
    assert "resolve_ticket" in ids
    flaky_ids = {t.id for t in suite.tasks if t.flaky}
    assert flaky_ids == {"resolve_ticket", "bump_replica_count"}


def test_regressed_suite_differs_only_in_rename_owner():
    good = load_suite("demo/suite.yaml")
    bad = load_suite("demo/suite_regressed.yaml")
    good_owner = good.get("rename_owner")
    bad_owner = bad.get("rename_owner")
    assert good_owner.plan != bad_owner.plan
    for tid in {t.id for t in good.tasks} - {"rename_owner"}:
        assert good.get(tid).plan == bad.get(tid).plan
