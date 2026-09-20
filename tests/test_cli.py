import json

from click.testing import CliRunner

from aeh.cli import main


def test_cli_run_report_baseline_gate_end_to_end(tmp_path):
    runner = CliRunner()
    cassette_dir = str(tmp_path / "cassettes")
    results_path = str(tmp_path / "results.json")
    baseline_path = str(tmp_path / "baseline.json")
    html_path = str(tmp_path / "report.html")

    result = runner.invoke(
        main, ["run", "--suite", "demo/suite.yaml", "--cassette-dir", cassette_dir, "--mode", "record", "--out", results_path]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["report", "--results", results_path, "--html-out", html_path])
    assert result.exit_code == 0, result.output
    assert "demo-suite" in result.output
    html = open(html_path).read()
    assert "<title>aeh report" in html

    result = runner.invoke(main, ["baseline", "save", "--results", results_path, "--out", baseline_path])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["gate", "--baseline", baseline_path, "--results", results_path])
    assert result.exit_code == 0, result.output
    assert "GATE PASSED" in result.output


def test_cli_gate_fails_on_regressed_suite(tmp_path):
    runner = CliRunner()
    baseline_cassettes = str(tmp_path / "c1")
    baseline_results = str(tmp_path / "r1.json")
    baseline_path = str(tmp_path / "baseline.json")
    regressed_cassettes = str(tmp_path / "c2")
    regressed_results = str(tmp_path / "r2.json")

    runner.invoke(main, ["run", "--suite", "demo/suite.yaml", "--cassette-dir", baseline_cassettes, "--mode", "record", "--out", baseline_results])
    runner.invoke(main, ["baseline", "save", "--results", baseline_results, "--out", baseline_path])
    runner.invoke(main, ["run", "--suite", "demo/suite_regressed.yaml", "--cassette-dir", regressed_cassettes, "--mode", "record", "--out", regressed_results])

    result = runner.invoke(main, ["gate", "--baseline", baseline_path, "--results", regressed_results])
    assert result.exit_code == 1
    assert "rename_owner" in result.output
    assert "GATE FAILED" in result.output


def test_cli_calibrate():
    runner = CliRunner()
    result = runner.invoke(main, ["calibrate", "--data", "data/calibration_set.jsonl"])
    assert result.exit_code == 0, result.output
    assert "kappa=" in result.output


def test_cli_replay_clean_when_seed_matches(tmp_path):
    runner = CliRunner()
    cassette_dir = str(tmp_path / "cassettes")
    runner.invoke(main, ["record", "--suite", "demo/suite.yaml", "--task", "fix_max_retries", "--seed", "1", "--out", cassette_dir])
    result = runner.invoke(main, ["replay", "--suite", "demo/suite.yaml", "--task", "fix_max_retries", "--seed", "1", "--cassette-dir", cassette_dir])
    assert result.exit_code == 0
    assert "replayed: 1 clean, 0 diverged" in result.output


def test_cli_replay_detects_divergence_and_exits_nonzero(tmp_path):
    import shutil

    runner = CliRunner()
    cassette_dir = str(tmp_path / "cassettes")
    runner.invoke(main, ["record", "--suite", "demo/suite.yaml", "--task", "fix_max_retries", "--seed", "1", "--out", cassette_dir])
    shutil.copy(f"{cassette_dir}/fix_max_retries-seed1.jsonl", f"{cassette_dir}/fix_max_retries-seed2.jsonl")

    result = runner.invoke(main, ["replay", "--suite", "demo/suite.yaml", "--task", "fix_max_retries", "--seed", "2", "--cassette-dir", cassette_dir])
    assert result.exit_code == 1
    assert "DIVERGED" in result.output
    assert "replayed: 0 clean, 1 diverged" in result.output
