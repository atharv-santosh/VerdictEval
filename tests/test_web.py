"""Route smoke tests, an end-to-end fake-backend suite run, and a gate flow test for the Eval Studio web app -- all offline, zero credentials."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aeh.web.server import create_app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    suite_path = tmp_path / "suite.yaml"
    shutil.copy(REPO_ROOT / "demo" / "suite.yaml", suite_path)
    calibration_path = tmp_path / "calibration_set.jsonl"
    shutil.copy(REPO_ROOT / "data" / "calibration_set.jsonl", calibration_path)
    cassette_dir = tmp_path / "cassettes"
    cassette_dir.mkdir()
    src_cassettes = REPO_ROOT / "cassettes" / "gemini-flakiness"
    for f in sorted(src_cassettes.glob("fix_max_retries-seed*.jsonl")):
        shutil.copy(f, cassette_dir / f.name)
    work_dir = tmp_path / "work"

    app = create_app(
        suite_path=str(suite_path),
        cassette_dir=str(cassette_dir),
        work_dir=str(work_dir),
        calibration_path=str(calibration_path),
    )
    with TestClient(app) as c:
        yield c


def test_index_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Eval Studio" in resp.text


def test_static_assets_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_mode_offline_with_no_credentials(client):
    resp = client.get("/api/mode")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gemini"]["ready"] is False
    assert "offline" in data["banner"].lower()


def test_get_suite_loads_demo_suite_by_default(client):
    resp = client.get("/api/suite")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "demo-suite"
    assert len(data["tasks"]) == 9


def test_task_authoring_round_trips_to_yaml(client, tmp_path):
    new_task = {
        "id": "web_authored_task",
        "input": "Set foo to bar in a.txt",
        "setup": {"workspace": {"a.txt": "foo: baz\n"}},
        "plan": [
            {"tool": "read_file", "args": {"path": "a.txt"}},
            {"tool": "write_file", "args": {"path": "a.txt", "content": "foo: bar\n"}},
            {"finish": {"status": "done"}},
        ],
        "assertions": [{"type": "file_contains", "path": "a.txt", "text": "foo: bar"}],
        "rubric": "did it set foo to bar",
        "seeds": [1, 2],
        "tags": ["web-test"],
    }
    resp = client.post("/api/suite/tasks", json={"task": new_task, "editing_id": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    suite_path = tmp_path / "suite.yaml"
    import yaml

    on_disk = yaml.safe_load(suite_path.read_text())
    assert any(t["id"] == "web_authored_task" for t in on_disk["tasks"])

    resp = client.get("/api/suite")
    assert any(t["id"] == "web_authored_task" for t in resp.json()["tasks"])

    resp = client.delete("/api/suite/tasks/web_authored_task")
    assert resp.status_code == 200
    on_disk = yaml.safe_load(suite_path.read_text())
    assert not any(t["id"] == "web_authored_task" for t in on_disk["tasks"])


def test_task_authoring_validates_inline(client):
    resp = client.post("/api/suite/tasks", json={"task": {"id": "", "input": ""}, "editing_id": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any("Task ID" in e for e in body["errors"])
    assert any("Prompt" in e for e in body["errors"])


def test_task_authoring_rejects_duplicate_id(client):
    resp = client.post(
        "/api/suite/tasks",
        json={
            "task": {"id": "fix_max_retries", "input": "x", "seeds": [1], "plan": [{"finish": {"status": "done"}}]},
            "editing_id": None,
        },
    )
    assert resp.json()["ok"] is False


def _wait_for_run(client, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["summary"]["status"] != "running":
            return data
        time.sleep(0.05)
    raise TimeoutError("run did not finish in time")


def test_fake_backend_run_end_to_end(client):
    resp = client.post("/api/runs", json={"backend": "fake"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    grid = _wait_for_run(client, run_id)
    assert grid["summary"]["status"] == "done"
    assert grid["summary"]["total_seed_runs"] == 45
    assert len(grid["rows"]) == 9

    fix_row = next(r for r in grid["rows"] if r["task_id"] == "fix_max_retries")
    assert fix_row["pass_rate"] == 1.0
    assert fix_row["verdict"] == "stable-pass"

    traj = client.get("/api/runs/{}/trajectory/fix_max_retries/1".format(run_id)).json()
    assert traj["passed"] is True
    assert traj["assertions"]
    assert any(e["type"] == "llm_call" for e in traj["events"])
    assert traj["judge"] is not None


def test_run_single_task_with_seed_override(client):
    resp = client.post("/api/runs", json={"task_ids": ["fix_max_retries"], "seeds": [7], "backend": "fake"})
    run_id = resp.json()["run_id"]
    grid = _wait_for_run(client, run_id)
    assert grid["summary"]["total_seed_runs"] == 1
    assert grid["rows"][0]["seeds"]["7"]["passed"] is True


def test_run_unknown_task_rejected(client):
    resp = client.post("/api/runs", json={"task_ids": ["does_not_exist"], "backend": "fake"})
    assert resp.status_code == 400


def test_gemini_backend_requires_credentials(client):
    resp = client.post("/api/runs", json={"backend": "gemini"})
    assert resp.status_code == 400
    assert "Gemini backend is not ready" in resp.json()["detail"]


def test_estimate_before_bulk_run(client):
    resp = client.post("/api/estimate", json={"model": "gemini-2.5-flash-lite"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["seed_runs"] == 45
    assert body["estimated_tokens_in"] > 0
    assert body["estimated_cost"] > 0


def test_baseline_and_gate_flow(client):
    resp = client.post("/api/runs", json={"backend": "fake"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    resp = client.get("/api/baseline")
    assert resp.json()["exists"] is False

    resp = client.post(f"/api/runs/{run_id}/baseline")
    assert resp.status_code == 200
    assert resp.json()["task_count"] == 9

    resp = client.get("/api/baseline")
    assert resp.json()["exists"] is True

    resp = client.post("/api/gate", json={"run_id": run_id})
    assert resp.status_code == 200
    gate = resp.json()
    assert gate["gate_passed"] is True
    assert all(d["classification"] == "unchanged" for d in gate["diffs"])

    assert all("margin" in d for d in gate["diffs"])


def test_gate_without_baseline_errors(client):
    resp = client.post("/api/runs", json={"backend": "fake"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)
    resp = client.post("/api/gate", json={"run_id": run_id})
    assert resp.status_code == 400


def test_calibration_offline_judge(client):
    resp = client.post("/api/calibration/run", json={"backend": "fake"})
    assert resp.status_code == 200
    result = resp.json()
    assert result["n"] == 23
    assert 0.0 <= result["kappa"] <= 1.0
    assert "confusion" in result


def test_calibration_relabel_changes_result(client):
    rows = client.get("/api/calibration").json()["rows"]
    row_id = rows[0]["id"]
    original_label = rows[0]["human_label"]
    flipped = "fail" if original_label == "pass" else "pass"

    before = client.post("/api/calibration/run", json={"backend": "fake"}).json()
    resp = client.post("/api/calibration/relabel", json={"id": row_id, "label": flipped})
    assert resp.status_code == 200
    after = client.post("/api/calibration/run", json={"backend": "fake"}).json()

    assert after["n"] == before["n"]
    rows_after = client.get("/api/calibration").json()["rows"]
    assert next(r for r in rows_after if r["id"] == row_id)["human_label"] == flipped

    client.post("/api/calibration/reset")
    rows_reset = client.get("/api/calibration").json()["rows"]
    assert next(r for r in rows_reset if r["id"] == row_id)["human_label"] == original_label


def test_cassette_listing(client):
    resp = client.get("/api/cassettes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cassettes"]
    assert all(c["task_id"] == "fix_max_retries" for c in data["cassettes"])


def test_cassette_view_events(client):
    files = client.get("/api/cassettes").json()["cassettes"]
    file_name = files[0]["file"]
    resp = client.get(f"/api/cassettes/{file_name}")
    assert resp.status_code == 200
    assert resp.json()["events"]


def test_cassette_replay_offline_no_credentials(client):
    files = client.get("/api/cassettes").json()["cassettes"]
    file_name = files[0]["file"]
    resp = client.post(f"/api/cassettes/{file_name}/replay", json={"backend": "gemini", "on_divergence": "passthrough"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "diverged" in body


def test_sse_stream_completes(client):
    resp = client.post("/api/runs", json={"backend": "fake"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    with client.stream("GET", f"/api/runs/{run_id}/stream") as stream:
        saw_end = False
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            if payload.get("type") == "run_end":
                saw_end = True
                break
        assert saw_end
