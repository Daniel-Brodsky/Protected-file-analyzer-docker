from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from protected_file_analyzer.config import get_settings
from protected_file_analyzer.pipeline import run_pipeline
from protected_file_analyzer.runners import WorkerResult
from protected_file_analyzer.store import JobStore


class FakeRunner:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def capabilities(self):
        return {"backend": "fake"}

    async def health(self):
        return {"status": "healthy"}

    async def run_worker(self, args, timeout=180):
        command = args[0]
        values = {args[i]: args[i + 1] for i in range(1, len(args) - 1, 2) if args[i].startswith('--')}
        self.calls.append((command, values))
        return await self.behavior(command, values)


def test_completed_pipeline_keeps_reveal_secret_and_hides_password(app_env):
    settings = get_settings()
    store = JobStore(settings)

    async def behavior(command, values):
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command == 'crack-mask':
            return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": False})
        if command == 'crack':
            Path(values['--pot']).write_text('fake-pot', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True, "found": True})
        if command == 'recover-secret':
            secret = Path(values['--output'])
            secret.write_text('lab-password', encoding='utf-8')
            secret.chmod(0o600)
            return WorkerResult(ok=True, payload={"ok": True, "secret_ready": True})
        if command == 'decrypt':
            secret = Path(values['--secret'])
            assert secret.read_text(encoding='utf-8') == 'lab-password'
            secret.unlink()
            target = Path(values['--output-dir']) / 'decrypted.pdf'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'decrypted')
            return WorkerResult(ok=True, payload={"ok": True, "scan_target": str(target)})
        if command == 'scan':
            report = Path(values['--report'])
            report.write_text(json.dumps({"summary": {"verdict": "no_obvious_findings"}}), encoding='utf-8')
            Path(values['--artifact']).write_bytes(b'artifact')
            return WorkerResult(ok=True, payload={
                "ok": True,
                "artifact_name": 'decrypted-analysis-copy.pdf',
                "summary": {"verdict": "no_obvious_findings"},
            })
        raise AssertionError(command)

    runner = FakeRunner(behavior)
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.zip", "wordlist_mode": "rockyou"})
    source = job_dir / 'input' / 'protected.zip'
    source.write_bytes(b'protected')
    store.update(job_id, source_relative='input/protected.zip', source_size=source.stat().st_size, wordlist_path=str(settings.default_rockyou_path))

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    reveal_secret = job_dir / 'work' / 'reveal-password.secret'
    assert state['status'] == 'completed'
    assert state['atomic_state'] == 'completed'
    assert state['password_available'] is True
    assert 'lab-password' not in json.dumps(state)
    assert reveal_secret.read_text(encoding='utf-8') == 'lab-password'
    assert not source.exists()


def test_pipeline_marks_timeout_without_scan(app_env):
    settings = get_settings()
    store = JobStore(settings)

    async def behavior(command, values):
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command == 'crack-israeli-id':
            return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": True})
        raise AssertionError(command)

    runner = FakeRunner(behavior)
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.pdf", "wordlist_mode": "israeli_id"})
    source = job_dir / 'input' / 'protected.pdf'
    source.write_bytes(b'protected')
    store.update(job_id, source_relative='input/protected.pdf', source_size=source.stat().st_size)

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    assert state['status'] == 'timed_out'
    assert state['atomic_state'] == 'failed'
    assert not source.exists()


def test_store_claim_pending_job_is_atomic(app_env):
    settings = get_settings()
    store = JobStore(settings)
    job_id = uuid.uuid4().hex
    store.create(job_id, {"original_name": "sample.zip", "wordlist_mode": "rockyou"})

    first = store.claim_next_pending()
    second = store.claim_next_pending()

    assert first is not None
    assert first['job_id'] == job_id
    assert first['status'] == 'running'
    assert first['atomic_state'] == 'running'
    assert second is None
