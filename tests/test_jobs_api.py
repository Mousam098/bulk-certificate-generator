"""Creating a generation job + job status/progress."""
from datetime import date

from app import service
from app.validation import ValidRecipient


def test_create_job_returns_202_with_location_and_counts(client):
    r = client.post("/jobs", json={
        "event_name": "Bootcamp", "recipients": [{"name": "A B"}, {"name": "C D"}],
    })
    assert r.status_code == 202
    body = r.json()
    assert r.headers["location"] == f"/jobs/{body['id']}"
    assert body["total"] == 2
    assert body["event_name"] == "Bootcamp"
    assert body["issuer"]  # default applied
    assert body["issue_date"]  # defaults to today


def test_job_status_after_processing(client, payload_factory):
    job_id = client.post("/jobs", json=payload_factory(4)).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert (job["total"], job["succeeded"], job["failed"], job["pending"]) == (4, 4, 0, 0)
    assert job["progress_percent"] == 100.0
    assert job["started_at"] and job["finished_at"]


def test_job_progress_while_pending(app, client):
    """A job that has been created but not processed reports 0% progress."""
    with app.state.session_factory() as db:
        job = service.create_job(
            db, event_name="E", issuer="I", issue_date=date.today(),
            valid=[ValidRecipient(0, "A", None), ValidRecipient(1, "B", None)], invalid=[],
        )
    body = client.get(f"/jobs/{job.id}").json()
    assert body["status"] == "pending"
    assert body["pending"] == 2
    assert body["progress_percent"] == 0.0


def test_unknown_job_is_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404
    assert client.get("/jobs/does-not-exist/certificates").status_code == 404


def test_list_jobs(client, payload_factory):
    ids = [client.post("/jobs", json=payload_factory(1)).json()["id"] for _ in range(3)]
    page = client.get("/jobs", params={"limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert {j["id"] for j in page["items"]} <= set(ids)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
