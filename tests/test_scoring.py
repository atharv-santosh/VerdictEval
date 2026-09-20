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
