"""Input validation: request level and per-recipient level."""
import pytest

from app.validation import validate_recipients


# ---- request level ---------------------------------------------------------
@pytest.mark.parametrize("override", [
    {"event_name": ""},
    {"event_name": "   "},
    {"recipients": []},
])
def test_invalid_request_is_rejected(client, payload_factory, override):
    assert client.post("/jobs", json=payload_factory(**override)).status_code == 422


def test_missing_fields_rejected(client):
    assert client.post("/jobs", json={}).status_code == 422
    assert client.post("/jobs", json={"event_name": "X"}).status_code == 422


def test_too_many_recipients_rejected(client, payload_factory):
    r = client.post("/jobs", json=payload_factory(51))  # limit is 50 in tests
    assert r.status_code == 422
    assert "Too many recipients" in r.json()["detail"]


def test_bad_issue_date_rejected(client, payload_factory):
    assert client.post("/jobs", json=payload_factory(issue_date="not-a-date")).status_code == 422


# ---- recipient level (unit) -----------------------------------------------
def test_valid_recipients_normalised():
    valid, invalid = validate_recipients([{"name": "  Asha Rao ", "email": " ASHA@Example.COM "}])
    assert not invalid
    assert (valid[0].name, valid[0].email) == ("Asha Rao", "asha@example.com")


def test_email_is_optional():
    valid, invalid = validate_recipients([{"name": "A"}, {"name": "B", "email": ""}])
    assert len(valid) == 2 and not invalid
    assert all(v.email is None for v in valid)


@pytest.mark.parametrize("bad, fragment", [
    ({"name": ""}, "name"),
    ({"name": "   "}, "name"),
    ({}, "name"),
    ({"name": 123}, "name"),
    ({"name": "x" * 101}, "name"),
    ({"name": "Bad\x00Name"}, "control characters"),
    ({"name": "Mousom", "email": "nope"}, "email"),
    ({"name": "Mousom", "email": "a@b"}, "email"),
    ({"name": "\u0986\u09b6\u09be"}, "cannot render"),  # Bengali: not in the PDF font
    ("just a string", "must be an object"),
    (None, "must be an object"),
])
def test_invalid_recipient_reports_reason(bad, fragment):
    valid, invalid = validate_recipients([bad])
    assert not valid
    assert fragment in invalid[0].error


def test_indexes_preserved_in_mixed_list():
    valid, invalid = validate_recipients([{"name": "ok"}, {"name": ""}, {"name": "ok2"}, 5])
    assert [v.index for v in valid] == [0, 2]
    assert [i.index for i in invalid] == [1, 3]


# ---- recipient level (API) ---------------------------------------------------
def test_invalid_recipients_do_not_block_valid_ones(client, payload_factory):
    payload = payload_factory(2)
    payload["recipients"] += [{"name": ""}, {"name": "Bob", "email": "bad"}]
    job_id = client.post("/jobs", json=payload).json()["id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "completed_with_errors"
    assert (job["succeeded"], job["failed"], job["total"]) == (2, 2, 4)

    failed = client.get(f"/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert [c["index"] for c in failed["items"]] == [2, 3]
    assert all("Validation failed" in c["error"] for c in failed["items"])
    assert all(c["download_url"] is None for c in failed["items"])


def test_all_invalid_recipients_returns_422_and_creates_no_job(client, payload_factory):
    payload = payload_factory(0, recipients=[{"name": ""}, {"email": "x@y.com"}])
    r = client.post("/jobs", json=payload)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert [e["index"] for e in errors] == [0, 1]
    assert client.get("/jobs").json()["total"] == 0
