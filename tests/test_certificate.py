"""Certificate (PDF) generation."""
from datetime import date

import pytest

from app.certificate import CertificateData, generate_certificate_pdf


def make_data(**kw):
    base = dict(
        certificate_id="cert-123", recipient_name="Asha Rao", event_name="Python Bootcamp",
        issuer="Example Academy", issue_date=date(2026, 1, 15),
    )
    base.update(kw)
    return CertificateData(**base)


def test_generates_valid_pdf_with_recipient_information(tmp_path):
    path = tmp_path / "sub" / "c.pdf"  # parent directory is created on demand
    generate_certificate_pdf(path, make_data())

    content = path.read_bytes()
    assert content.startswith(b"%PDF")
    assert content.rstrip().endswith(b"%%EOF")
    for expected in (b"Asha Rao", b"Python Bootcamp", b"Example Academy",
                     b"15 January 2026", b"cert-123"):
        assert expected in content
    assert not list(path.parent.glob("*.tmp"))


def test_special_characters_are_escaped(tmp_path):
    path = tmp_path / "c.pdf"
    generate_certificate_pdf(path, make_data(recipient_name="Anne (Ann) O'Neil <b>& Co"))
    assert path.read_bytes().startswith(b"%PDF")


def test_very_long_name_and_event_still_render(tmp_path):
    path = tmp_path / "c.pdf"
    generate_certificate_pdf(
        path, make_data(recipient_name="W" * 100, event_name="Long Event Title " * 12)
    )
    assert path.stat().st_size > 0


def test_failed_generation_leaves_no_partial_file(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(OSError):
        generate_certificate_pdf(blocker / "c.pdf", make_data())
    assert [p.name for p in tmp_path.iterdir()] == ["not-a-dir"]


def test_job_generates_one_file_per_valid_recipient(client, settings, payload_factory):
    job_id = client.post("/jobs", json=payload_factory(3)).json()["id"]
    files = sorted((settings.storage_dir / job_id).glob("*.pdf"))
    assert len(files) == 3
    assert all(f.read_bytes().startswith(b"%PDF") for f in files)
