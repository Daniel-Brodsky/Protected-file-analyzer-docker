from __future__ import annotations

from fastapi.testclient import TestClient

from protected_file_analyzer.app import create_app
from protected_file_analyzer.config import get_settings
from protected_file_analyzer.store import JobStore


def test_create_job_accepts_optional_custom_wordlist_without_strategy_selection(app_env):
    settings = get_settings()
    store = JobStore(settings)
    app = create_app(settings=settings, store=store)

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            data={"authorization_confirmed": "true"},
            files={
                "protected_file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf"),
                "custom_wordlist": ("custom.txt", b"guess-me\n", "text/plain"),
            },
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"job_id", "status_url"}
    state = store.get(body["job_id"])
    assert state["status"] == "pending"
    assert state["custom_wordlist_supplied"] is True
    assert state["format"] == "pdf"
    assert "wordlist_mode" not in state


def test_capabilities_report_wordlist_availability_and_cancel_endpoint(app_env):
    settings = get_settings()
    store = JobStore(settings)
    app = create_app(settings=settings, store=store)

    with TestClient(app) as client:
        create = client.post(
            "/api/jobs",
            data={"authorization_confirmed": "true"},
            files={"protected_file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        job_id = create.json()["job_id"]
        response = client.get("/api/capabilities")
        cancel = client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["runner_backend"] == "local"
    assert body["wordlists"]["rockyou"] is True
    assert body["wordlists"]["custom_upload"] is True
    assert cancel.status_code == 202
    assert cancel.json()["status"] == "cancelled"


def test_delete_running_job_requires_cancel_first(app_env):
    settings = get_settings()
    store = JobStore(settings)
    app = create_app(settings=settings, store=store)
    job_id = 'a' * 32
    store.create(job_id, {"original_name": "sample.pdf", "format": "pdf", "custom_wordlist_supplied": False})
    store.update(job_id, status='running', atomic_state='running', source_relative='input/protected.pdf')

    with TestClient(app) as client:
        response = client.delete(f'/api/jobs/{job_id}')

    assert response.status_code == 409
    assert 'Cancel the analysis before deleting it' in response.json()['detail']
