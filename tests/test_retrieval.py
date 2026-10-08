"""Retrieving generated certificates."""
from datetime import date
import io
import zipfile

from app import service
from app.validation import ValidRecipient
from tests.test_processing import flaky_generator


def _certs(client, job_id, **params):
    return client.get(f"/jobs/{job_id}/certificates", params=params).json()


def test_download_single_certificate(client, payload_factory):
    job_id = client.post("/jobs", json=payload_factory(2)).json()["id"]
    cert = _certs(client, job_id)["items"][0]

    r = client.get(cert["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "certificate-person-0.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF") and b"Person 0" in r.content


def test_download_unknown_certificate_is_404(client):
    assert client.get("/certificates/nope/download").status_code == 404


def test_download_failed_certificate_is_409(app, client, payload_factory):
    app.state.generator = flaky_generator("Person 0")
    job_id = client.post("/jobs", json=payload_factory(2)).json()["id"]
    failed = _certs(client, job_id, status="failed")["items"][0]
    assert client.get(f"/certificates/{failed['id']}/download").status_code == 409


def test_missing_file_on_disk_is_404(client, payload_factory, settings):
    job_id = client.post("/jobs", json=payload_factory(1)).json()["id"]
    cert = _certs(client, job_id)["items"][0]
    next((settings.storage_dir / job_id).glob("*.pdf")).unlink()
    assert client.get(cert["download_url"]).status_code == 404


def test_zip_contains_only_successful_certificates(app, client, payload_factory):
    app.state.generator = flaky_generator("Person 1")
    job_id = client.post("/jobs", json=payload_factory(3)).json()["id"]

    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.namelist() == ["0001-person-0.pdf", "0003-person-2.pdf"]
        assert all(z.read(n).startswith(b"%PDF") for n in z.namelist())


def test_zip_not_available_while_job_unfinished(app, client):
    with app.state.session_factory() as db:
        job = service.create_job(
            db, event_name="E", issuer="I", issue_date=date.today(),
            valid=[ValidRecipient(0, "A", None)], invalid=[],
        )
    assert client.get(f"/jobs/{job.id}/download").status_code == 409


def test_zip_404_when_nothing_was_generated(app, client, payload_factory):
    def always_fail(path, data):
        raise RuntimeError("x")

    app.state.generator = always_fail
    job_id = client.post("/jobs", json=payload_factory(2)).json()["id"]
    assert client.get(f"/jobs/{job_id}/download").status_code == 404


def test_certificate_listing_filter_and_pagination(app, client, payload_factory):
    app.state.generator = flaky_generator("Person 2")
    job_id = client.post("/jobs", json=payload_factory(5)).json()["id"]

    assert _certs(client, job_id)["total"] == 5
    assert _certs(client, job_id, status="succeeded")["total"] == 4
    assert _certs(client, job_id, status="failed")["total"] == 1

    page = _certs(client, job_id, limit=2, offset=3)
    assert page["total"] == 5
    assert [c["index"] for c in page["items"]] == [3, 4]
    assert client.get(f"/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422
