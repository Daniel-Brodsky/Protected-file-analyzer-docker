from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from protected_file_analyzer.app import create_app
from protected_file_analyzer.store import JobStore
from protected_file_analyzer.config import get_settings


def test_reveal_password_is_explicit_and_not_cached(app_env):
    settings = get_settings()
    store = JobStore(settings)
    app = create_app(settings=settings, store=store)

    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.zip", "wordlist_mode": "rockyou"})
    secret = job_dir / "work" / "reveal-password.secret"
    secret.write_text("correct horse battery staple", encoding="utf-8")
    secret.chmod(0o600)
    store.update(job_id, status="completed", atomic_state="completed", password_available=True)

    with TestClient(app) as client:
        status = client.get(f"/api/jobs/{job_id}")
        assert status.status_code == 200
        assert "password" not in status.json()

        response = client.post(f"/api/jobs/{job_id}/reveal-password")
        assert response.status_code == 200
        assert response.json() == {
            "password": "correct horse battery staple",
            "display_seconds": 30,
        }
        assert "no-store" in response.headers["cache-control"]

        audited = store.get(job_id)
        assert audited["reveal_count"] == 1
        assert "last_revealed_at" in audited


def test_capabilities_endpoint_reports_runner_and_tools(app_env):
    settings = get_settings()
    store = JobStore(settings)
    app = create_app(settings=settings, store=store)

    with TestClient(app) as client:
        response = client.get("/api/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["runner_backend"] == "local"
    assert "formats" in body
    assert "scanners" in body
    assert "wordlists" in body
