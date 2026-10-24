import json
import time

import pytest
from fastapi.testclient import TestClient
from job_finder.api import create_app
from job_finder.cli import app as cli
from job_finder.service import Service
from typer.testing import CliRunner


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Service(tmp_path), testing=True)) as client:
        yield client


def headers(client):
    return {"X-Session-Token": client.get("/api/session").json()["token"]}


def test_actual_ui_assets_and_health_are_served(client):
    assert client.get("/").status_code == 200
    assert "text/css" in client.get("/static/app.css").headers["content-type"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/health/ready").json()["ready"]
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Job Finder"


def test_mutations_need_session_and_same_origin(client):
    assert client.post("/api/demo").status_code == 403
    assert client.post("/api/demo", headers=headers(client) | {"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/demo", headers=headers(client)).status_code == 200


def test_api_demo_run_review_tracking_export_roundtrip(client):
    token = headers(client)
    demo = client.post("/api/demo", headers=token).json()
    search, run = demo["search"], demo["run"]
    for _ in range(200):
        result = client.get(f"/api/runs/{run['id']}").json()
        if result["run"]["status"] not in {"running", "queued"}:
            break
        time.sleep(0.03)
    assert result["run"]["status"] == "completed", result
    detail = client.get(f"/api/searches/{search['id']}").json()
    assert detail["counts"]["drafted"] >= 1
    assert len(detail["opportunities"]) >= 3
    opportunity = next(row for row in detail["opportunities"] if row["drafts"] and not row["qa_issues"])
    approved = client.patch(f"/api/opportunities/{opportunity['id']}", json={"status": "approved"}, headers=token)
    assert approved.status_code == 200
    assert not approved.json()["ready_to_contact"]
    tracked = client.patch(f"/api/opportunities/{opportunity['id']}", json={"application_status": "shortlisted", "next_follow_up": "2027-01-01"}, headers=token)
    assert tracked.status_code == 200
    assert tracked.json()["application_status"] == "shortlisted"
    exported = client.get(f"/api/searches/{search['id']}/export?format=csv")
    assert exported.status_code == 200
    assert "apply_url" in exported.text
    assert client.get(f"/api/searches/{search['id']}/export?scope=outreach").status_code == 400


def test_document_and_opportunity_import_via_real_multipart(client):
    token = headers(client)
    search = client.post("/api/searches", json={"name": "Import", "objective": "Manufacturing engineering"}, headers=token).json()
    root = f"/api/searches/{search['id']}"
    response = client.post(root + "/documents", files={"file": ("resume.md", b"I design tooling and improve manufacturing processes.", "text/plain")}, headers=token)
    assert response.status_code == 200
    doc = response.json()
    response = client.post(root + "/import", files={"file": ("jobs.csv", b"company,title,listing_url\nFixture,Manufacturing Engineer,https://fixture.example/jobs/1\n", "text/csv")}, headers=token)
    assert response.json()["imported"] == 1
    assert client.delete(root + "/documents/" + doc["id"], headers=token).status_code == 200


def test_resume_intake_saves_file_and_critical_preferences_together(client):
    payload = {"name": "Resume search", "preferences": {
        "locations": ["Los Angeles, CA", "San Diego, CA"], "salary_min": 120000,
        "work_modes": ["hybrid"], "roles": ["Mechanical Engineer"],
    }}
    response = client.post("/api/searches/intake", data={"payload": json.dumps(payload)},
        files={"file": ("resume.txt", b"Fictional engineer with CAD and test fixture experience.", "text/plain")},
        headers=headers(client))
    assert response.status_code == 200
    saved = client.get("/api/searches/" + response.json()["id"]).json()
    assert saved["search"]["preferences"]["salary_min"] == 120000
    assert saved["search"]["preferences"]["locations"] == payload["preferences"]["locations"]
    assert saved["documents"][0]["name"] == "resume.txt"
    assert not saved["search"]["demo"]


def test_invalid_resume_or_payload_does_not_create_partial_search(client):
    token = headers(client)
    for payload, filename, raw in (({"name": "Broken resume"}, "resume.pdf", b"not a pdf"), ({"name": ""}, "resume.txt", b"private candidate text")):
        response = client.post("/api/searches/intake", data={"payload": json.dumps(payload)},
            files={"file": (filename, raw, "application/octet-stream")}, headers=token)
        assert response.status_code in {400, 422}
        assert "private candidate text" not in response.text
        assert client.get("/api/searches").json()["searches"] == []
    response = client.post("/api/searches/intake", data={"payload": "invalid json"}, headers=token)
    assert response.status_code == 422


def test_intake_also_accepts_goals_without_a_resume(client):
    payload = {"name": "Goal search", "objective": "Design test fixtures", "preferences": {"locations": ["Remote US"], "salary_min": 90000}}
    response = client.post("/api/searches/intake", data={"payload": json.dumps(payload)}, headers=headers(client))
    assert response.status_code == 200
    assert response.json()["objective"] == payload["objective"]


def test_validation_errors_do_not_echo_resume_inputs(client):
    response = client.post("/api/searches", json={"name": "", "candidate": {"resume_text": "private resume text"}}, headers=headers(client))
    assert response.status_code == 422
    assert "private resume text" not in response.text
    assert response.json()["error"]["code"] == "INPUT-INVALID"


def test_cli_json_is_clean_and_shares_persistence(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--data-dir", str(tmp_path), "demo", "--run", "--json"])
    assert result.exit_code == 0, result.output
    run = json.loads(result.stdout)["run"]
    assert run["status"] == "completed"
    assert Service(tmp_path).search_detail(run["search_id"])["counts"]["drafted"] >= 1
    path = tmp_path / "research.csv"
    exported = runner.invoke(cli, ["--data-dir", str(tmp_path), "export", run["search_id"], "--output", str(path), "--json"])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout)["bytes"] > 1000
    assert path.exists()


def test_cli_errors_have_stable_exit_and_json(tmp_path):
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "show", "missing", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "NOT-FOUND"


def test_cli_create_config_routes_resume_and_activity_use_shared_services(tmp_path):
    runner = CliRunner()
    config = tmp_path / "search.json"
    config.write_text(json.dumps({
        "name": "Engineer search", "objective": "Build aerospace manufacturing systems",
        "candidate": {"name": "Taylor Example", "skills": ["CNC", "Python"]},
        "settings": {"provider": "codex", "routes": {"research": {"provider": "claude", "model": "research-model"}}},
    }))
    resume = tmp_path / "resume.md"
    resume.write_text("Taylor Example designed inspection fixtures and wrote Python analysis tools.")
    result = runner.invoke(cli, [
        "--data-dir", str(tmp_path / "data"), "search", "create", "--config", str(config),
        "--resume", str(resume), "--role", "Manufacturing Engineer", "--location", "Dayton, OH",
        "--work-mode", "hybrid", "--strict-work-mode", "--needs-sponsorship",
        "--outreach-provider", "codex", "--outreach-model", "draft-model", "--target", "30", "--json",
    ])
    assert result.exit_code == 0, result.output
    search = json.loads(result.stdout)
    assert search["settings"]["target_results"] == 30
    assert search["settings"]["routes"]["research"] == {"provider": "claude", "model": "research-model"}
    assert search["settings"]["routes"]["outreach"] == {"provider": "codex", "model": "draft-model"}
    assert search["candidate"]["needs_sponsorship"] is True
    assert search["preferences"]["strict_work_mode"] is True
    service = Service(tmp_path / "data")
    assert len(service.store.documents(search["id"])) == 1
    from job_finder.models import OpportunitySeed
    opportunity, _ = service.upsert_seed(search["id"], OpportunitySeed(company="Fixture", title="Engineer", listing_url="https://fixture.example/jobs/123"))
    tracked = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "activity", opportunity["id"], "--status", "applied", "--follow-up", "2027-01-01", "--json"])
    assert tracked.exit_code == 0, tracked.output
    assert json.loads(tracked.stdout)["application_status"] == "applied"
    assert service.store.get("opportunities", opportunity["id"])["next_follow_up"] == "2027-01-01"


def test_cli_nonobject_configuration_and_invalid_routes_fail_cleanly(tmp_path):
    runner = CliRunner()
    config = tmp_path / "bad.json"
    config.write_text("[]")
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "search", "create", "--config", str(config), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "INPUT-INVALID"
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "search", "create", "--research-provider", "unsupported", "--json"])
    assert result.exit_code == 2
    assert Service(tmp_path / "data").store.list("searches") == []


def test_cli_schema_and_export_overwrite_guard(tmp_path):
    runner = CliRunner()
    schema = runner.invoke(cli, ["schema", "search", "--json"])
    assert schema.exit_code == 0
    assert "candidate" in json.loads(schema.stdout)["properties"]
    existing = tmp_path / "keep.csv"
    existing.write_text("Keep this data")
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "export", "missing", "--output", str(existing), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "FILE-EXISTS"
    assert existing.read_text() == "Keep this data"
