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
        self.calls.append((command, values, timeout))
        return await self.behavior(command, values)


def test_pipeline_uses_bounded_policy_generic_stages_and_cleans_secrets(app_env):
    settings = get_settings()
    store = JobStore(settings)
    mounted_wordlist = settings.wordlists_dir / 'org.txt'
    mounted_wordlist.write_text('org-guess\n', encoding='utf-8')

    async def behavior(command, values):
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command == 'crack':
            wordlist = Path(values['--wordlist'])
            if wordlist.name == 'custom-wordlist.txt':
                return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": False, "cancelled": False})
            if wordlist.name == 'org.txt':
                pot = Path(values['--pot'])
                pot.write_text('fake-pot', encoding='utf-8')
                (Path(values['--workdir']) / 'john-session.log').write_text('session\n', encoding='utf-8')
                return WorkerResult(ok=True, payload={"ok": True, "found": True, "timed_out": False, "cancelled": False})
            raise AssertionError(f'unexpected wordlist {wordlist}')
        if command == 'recover-secret':
            secret = Path(values['--output'])
            secret.write_text('lab-password', encoding='utf-8')
            secret.chmod(0o600)
            return WorkerResult(ok=True, payload={"ok": True, "secret_ready": True})
        if command == 'decrypt':
            secret = Path(values['--secret'])
            assert secret.read_text(encoding='utf-8') == 'lab-password'
            target = Path(values['--output-dir']) / 'decrypted.pdf'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'decrypted')
            return WorkerResult(ok=True, payload={"ok": True, "scan_target": str(target)})
        if command == 'scan':
            report = Path(values['--report'])
            report.write_text(json.dumps({"summary": {"verdict": "no_obvious_findings"}, "tool_cards": []}), encoding='utf-8')
            Path(values['--artifact']).write_bytes(b'artifact')
            return WorkerResult(ok=True, payload={
                "ok": True,
                "artifact_name": 'decrypted-analysis-copy.pdf',
                "summary": {"verdict": "no_obvious_findings"},
            })
        raise AssertionError(command)

    runner = FakeRunner(behavior)
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.pdf", "format": "pdf", "custom_wordlist_supplied": True})
    source = job_dir / 'input' / 'protected.pdf'
    source.write_bytes(b'protected')
    custom_wordlist = job_dir / 'input' / 'custom-wordlist.txt'
    custom_wordlist.write_text('guess\n', encoding='utf-8')
    store.update(job_id, source_relative='input/protected.pdf', source_size=source.stat().st_size, custom_wordlist_path=str(custom_wordlist))

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    work_dir = job_dir / 'work'
    crack_calls = [(command, Path(values['--wordlist']).name, values['--max-candidates'], values['--timeout']) for command, values, _ in runner.calls if command == 'crack']

    assert state['status'] == 'completed'
    assert state['atomic_state'] == 'completed'
    assert state['stage'] == 'completed'
    assert state['message'] == 'Completed'
    assert crack_calls == [
        ('crack', 'custom-wordlist.txt', str(settings.recovery_custom_max_candidates), str(settings.recovery_custom_timeout_seconds)),
        ('crack', 'org.txt', str(settings.recovery_mounted_max_candidates), str(settings.recovery_mounted_timeout_seconds)),
    ]
    assert all(command != 'crack-scoped-id-patterns' for command, _, _ in runner.calls)
    assert all('password' not in key for key in state)
    assert 'lab-password' not in json.dumps(state)
    assert not source.exists()
    assert not custom_wordlist.exists()
    assert not (work_dir / 'password.secret').exists()
    assert not (work_dir / 'john.pot').exists()
    assert not (work_dir / 'hash.txt').exists()
    assert not any(work_dir.glob('john-session*'))


def test_pipeline_marks_generic_failure_when_policy_cannot_recover_access(app_env):
    settings = get_settings()
    store = JobStore(settings)

    async def behavior(command, values):
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command in {'crack', 'crack-mask', 'crack-scoped-id-patterns'}:
            return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": False, "cancelled": False})
        raise AssertionError(command)

    runner = FakeRunner(behavior)
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.pdf", "format": "pdf", "custom_wordlist_supplied": False})
    source = job_dir / 'input' / 'protected.pdf'
    source.write_bytes(b'protected')
    store.update(job_id, source_relative='input/protected.pdf', source_size=source.stat().st_size)

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    assert state['status'] == 'failed'
    assert state['atomic_state'] == 'failed'
    assert state['stage'] == 'completed'
    assert state['message'] == 'Unable to recover access with configured policy'
    assert 'rockyou' not in state['message'].lower()
    assert 'pin' not in state['message'].lower()
    assert 'israeli' not in state['message'].lower()


def test_pipeline_cancellation_marks_terminal_cancelled_and_cleans_runtime(app_env):
    settings = get_settings()
    store = JobStore(settings)
    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.pdf", "format": "pdf", "custom_wordlist_supplied": False})
    source = job_dir / 'input' / 'protected.pdf'
    source.write_bytes(b'protected')
    store.update(job_id, source_relative='input/protected.pdf', source_size=source.stat().st_size)

    async def behavior(command, values):
        if command == 'extract-hash':
            Path(values['--output']).write_text('fake-hash', encoding='utf-8')
            return WorkerResult(ok=True, payload={"ok": True})
        if command in {'crack', 'crack-mask'}:
            store.request_cancel(job_id)
            return WorkerResult(ok=True, payload={"ok": True, "found": False, "timed_out": False, "cancelled": True})
        raise AssertionError(command)

    runner = FakeRunner(behavior)
    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    assert state['status'] == 'cancelled'
    assert state['atomic_state'] == 'cancelled'
    assert state['message'] == 'Cancelled'
    assert not source.exists()
    assert not (job_dir / '.cancel').exists()
    assert not (job_dir / 'report.json').exists()
    assert not (job_dir / 'artifact.bin').exists()


def test_store_claim_pending_job_is_atomic(app_env):
    settings = get_settings()
    store = JobStore(settings)
    job_id = uuid.uuid4().hex
    store.create(job_id, {"original_name": "sample.zip", "format": "zip", "custom_wordlist_supplied": False})

    first = store.claim_next_pending()
    second = store.claim_next_pending()

    assert first is not None
    assert first['job_id'] == job_id
    assert first['status'] == 'running'
    assert first['atomic_state'] == 'running'
    assert second is None
