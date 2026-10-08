import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "certificates",
        max_recipients_per_job=50,
        resume_on_startup=False,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # No ``with`` block: lifespan (startup recovery) is not needed. FastAPI background
    # tasks still run, synchronously, right after each response - so a GET issued after
    # a POST sees the finished job.
    return TestClient(app)


def make_payload(n=3, **overrides):
    payload = {
        "event_name": "Test Event",
        "issuer": "Test Issuer",
        "issue_date": "2026-01-15",
        "recipients": [
            {"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(n)
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def payload_factory():
    return make_payload
