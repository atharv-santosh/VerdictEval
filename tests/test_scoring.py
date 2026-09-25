from aeh.scoring.assertions import evaluate_assertions
from aeh.scoring.judge import OfflineRubricJudge


def test_file_contains_pass_and_fail():
    fs = {"workspace": {"a.txt": "hello world"}}
    results = evaluate_assertions(fs, [], [{"type": "file_contains", "path": "a.txt", "text": "hello"}])
    assert results[0].passed

    results = evaluate_assertions(fs, [], [{"type": "file_contains", "path": "a.txt", "text": "nope"}])
    assert not results[0].passed


def test_file_equals():
    fs = {"workspace": {"a.txt": "exact"}}
    assert evaluate_assertions(fs, [], [{"type": "file_equals", "path": "a.txt", "content": "exact"}])[0].passed
    assert not evaluate_assertions(fs, [], [{"type": "file_equals", "path": "a.txt", "content": "different"}])[0].passed


def test_tool_sequence_exact_and_subsequence():
    ts = ["read_file", "write_file"]
    assert evaluate_assertions({}, ts, [{"type": "tool_sequence", "sequence": ts, "mode": "exact"}])[0].passed
    assert not evaluate_assertions({}, ts, [{"type": "tool_sequence", "sequence": ["write_file"], "mode": "exact"}])[0].passed

    ts2 = ["list_files", "read_file", "write_file"]
    assert evaluate_assertions({}, ts2, [{"type": "tool_sequence", "sequence": ["read_file", "write_file"], "mode": "subsequence"}])[0].passed


def test_forbidden_tool():
    ts = ["read_file", "write_file"]
    assert evaluate_assertions({}, ts, [{"type": "forbidden_tool", "tool": "search"}])[0].passed
    assert not evaluate_assertions({}, ts, [{"type": "forbidden_tool", "tool": "read_file"}])[0].passed


def test_status_equals():
    fs = {"status": "done"}
    assert evaluate_assertions(fs, [], [{"type": "status_equals", "status": "done"}])[0].passed
    assert not evaluate_assertions(fs, [], [{"type": "status_equals", "status": "incomplete"}])[0].passed


def test_offline_judge_matches_substring_anywhere_in_workspace():
    judge = OfflineRubricJudge()
    fs = {"workspace": {"a.txt": "max_retries: 5"}}
    verdict = judge.score({"expect_substring": "max_retries: 5"}, fs)
    assert verdict.label == "pass"

    verdict = judge.score({"expect_substring": "max_retries: 5"}, {"workspace": {}})
    assert verdict.label == "fail"


def test_tool_sequence_subsequence_tolerates_exploration():
    """A model that explores before acting still satisfies the intent the task cares about."""
    explored = ["search", "list_files", "read_file", "search", "write_file"]
    spec = {"type": "tool_sequence", "sequence": ["read_file", "write_file"], "mode": "subsequence"}
    assert evaluate_assertions({}, explored, [spec])[0].passed
    assert not evaluate_assertions({}, explored, [{**spec, "mode": "exact"}])[0].passed


def test_tool_sequence_subsequence_still_enforces_order():
    spec = {"type": "tool_sequence", "sequence": ["read_file", "write_file"], "mode": "subsequence"}
    assert not evaluate_assertions({}, ["write_file", "read_file"], [spec])[0].passed


def test_tool_sequence_set_mode_ignores_order():
    spec = {"type": "tool_sequence", "sequence": ["read_file", "write_file"], "mode": "set"}
    assert evaluate_assertions({}, ["write_file", "search", "read_file"], [spec])[0].passed
    assert not evaluate_assertions({}, ["write_file"], [spec])[0].passed


def test_required_tools_is_order_free_containment():
    spec = {"type": "required_tools", "tools": ["write_file"]}
    assert evaluate_assertions({}, ["search", "list_files", "write_file"], [spec])[0].passed

    result = evaluate_assertions({}, ["search", "list_files"], [spec])[0]
    assert not result.passed
    assert "write_file" in result.message


def test_required_tools_reports_every_missing_tool():
    spec = {"type": "required_tools", "tools": ["search", "write_file"]}
    result = evaluate_assertions({}, ["read_file"], [spec])[0]
    assert not result.passed
    assert "search" in result.message and "write_file" in result.message


def test_unknown_tool_sequence_mode_is_an_error():
    import pytest

    with pytest.raises(ValueError):
        evaluate_assertions({}, [], [{"type": "tool_sequence", "sequence": [], "mode": "fuzzy"}])
