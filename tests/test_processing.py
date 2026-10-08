"""Failure handling and recovery of background processing."""
from datetime import date

from app import service
from app.certificate import generate_certificate_pdf
from app.models import Certificate, Job
from app.validation import ValidRecipient


def flaky_generator(bad_name):
    def generate(path, data):
        if data.recipient_name == bad_name:
            raise RuntimeError("boom")
        generate_certificate_pdf(path, data)
    return generate


def test_single_certificate_failure_does_not_stop_the_job(app, client, payload_factory, settings):
    app.state.generator = flaky_generator("Person 1")
    job_id = client.post("/jobs", json=payload_factory(4)).json()["id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "completed_with_errors"
    assert (job["succeeded"], job["failed"], job["pending"]) == (3, 1, 0)

    certs = client.get(f"/jobs/{job_id}/certificates").json()["items"]
    by_name = {c["recipient_name"]: c for c in certs}
    assert by_name["Person 1"]["status"] == "failed"
    assert "RuntimeError: boom" in by_name["Person 1"]["error"]
    assert by_name["Person 1"]["download_url"] is None
    assert all(by_name[n]["status"] == "succeeded" for n in ("Person 0", "Person 2", "Person 3"))
    assert len(list((settings.storage_dir / job_id).glob("*.pdf"))) == 3  # others were written


def test_job_fails_when_every_certificate_fails(app, client, payload_factory):
    def always_fail(path, data):
        raise RuntimeError("nope")

    app.state.generator = always_fail
    job_id = client.post("/jobs", json=payload_factory(2)).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert (job["status"], job["failed"], job["succeeded"]) == ("failed", 2, 0)


def _make_unfinished_job(app, n):
    with app.state.session_factory() as db:
        return service.create_job(
            db, event_name="E", issuer="I", issue_date=date(2026, 1, 1),
            valid=[ValidRecipient(i, f"P{i}", None) for i in range(n)], invalid=[],
        ).id


def test_unfinished_jobs_are_resumed(app, client, settings):
    job_id = _make_unfinished_job(app, 3)
    # Simulate a crash mid-job: status says "processing", nothing generated yet.
    with app.state.session_factory() as db:
        db.get(Job, job_id).status = "processing"
        db.commit()

    resumed = service.recover_unfinished_jobs(
        app.state.session_factory, settings.storage_dir, app.state.generator
    )
    assert resumed == 1
    job = client.get(f"/jobs/{job_id}").json()
    assert (job["status"], job["succeeded"]) == ("completed", 3)


def test_resume_skips_already_generated_certificates(app, settings):
    job_id = _make_unfinished_job(app, 3)
    calls = []

    def counting(path, data):
        calls.append(data.recipient_name)
        generate_certificate_pdf(path, data)

    # First run only gets through part of the job (simulated by pre-completing one cert).
    with app.state.session_factory() as db:
        cert = db.query(Certificate).filter_by(job_id=job_id, index=0).one()
        cert.status, cert.file_path = "succeeded", f"{job_id}/{cert.id}.pdf"
        job = db.get(Job, job_id)
        job.succeeded, job.status = 1, "processing"
        db.commit()

    service.process_job(job_id, app.state.session_factory, settings.storage_dir, counting)

    assert calls == ["P1", "P2"]
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        assert (job.status, job.succeeded, job.failed) == ("completed", 3, 0)


def test_finished_jobs_are_not_reprocessed(app, client, payload_factory, settings):
    job_id = client.post("/jobs", json=payload_factory(2)).json()["id"]
    calls = []
    service.process_job(
        job_id, app.state.session_factory, settings.storage_dir, lambda p, d: calls.append(d)
    )
    assert calls == []
