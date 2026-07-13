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
    async def health(self):
        return {"status": "healthy"}

    def capabilities(self):
        return {"backend": "fake"}

    async def run_worker(self, args, timeout=180):
        command = args[0]
        values = {args[i]: args[i + 1] for i in range(1, len(args) - 1, 2) if args[i].startswith('--')}
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command == 'crack-mask':
            return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": False})
        if command == 'crack':
            Path(values['--pot']).write_text('fake-pot', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True, "found": True})
        if command == 'recover-secret':
            Path(values['--output']).write_text('test-password-1234', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True, "secret_ready": True})
        if command == 'decrypt':
            secret = Path(values['--secret'])
            assert secret.read_text(encoding='utf-8') == 'test-password-1234'
            secret.unlink()
            target = Path(values['--output-dir']) / 'decrypted.pdf'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'decrypted')
            return WorkerResult(ok=True, payload={"ok": True, "scan_target": str(target)})
        if command == 'scan':
            report = Path(values['--report'])
            report.write_text(json.dumps({"summary": {"verdict": "clean"}}), encoding='utf-8')
            Path(values['--artifact']).write_bytes(b'artifact')
            return WorkerResult(ok=True, payload={"ok": True, "artifact_name": 'artifact.pdf', "summary": {"verdict": "clean"}})
        raise AssertionError(command)


def test_password_never_appears_in_status_or_report(app_env):
    settings = get_settings()
    store = JobStore(settings)
    runner = FakeRunner()
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.zip", "wordlist_mode": "rockyou"})
    source = job_dir / 'input' / 'protected.zip'
    source.write_bytes(b'protected')
    store.update(job_id, source_relative='input/protected.zip', source_size=source.stat().st_size, wordlist_path=str(settings.default_rockyou_path))

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state_text = (job_dir / 'status.json').read_text(encoding='utf-8')
    report_text = (job_dir / 'report.json').read_text(encoding='utf-8')
    assert 'test-password-1234' not in state_text
    assert 'test-password-1234' not in report_text
