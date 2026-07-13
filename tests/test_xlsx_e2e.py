from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from protected_file_analyzer.app import create_app
from protected_file_analyzer.config import get_settings
from protected_file_analyzer.pipeline import run_pipeline
from protected_file_analyzer.runners import LocalContainerRunner
from protected_file_analyzer.store import JobStore
from protected_file_analyzer.tool_probe import TEST_PASSWORD


OFFICE_FIXTURE = Path(__file__).with_name('fixtures') / 'protected-sample.xlsx'


def test_xlsx_end_to_end_local_runner(app_env, tmp_path: Path):
    settings = get_settings()
    store = JobStore(settings)
    runner = LocalContainerRunner(settings)
    app = create_app(settings=settings, store=store, runner=runner)
    assert OFFICE_FIXTURE.exists()

    before_leftovers = {p.name for p in Path('/tmp').glob('john-home-*')}

    job_id = uuid.uuid4().hex
    job_dir = store.create(job_id, {"original_name": "sample.xlsx", "format": "xlsx", "custom_wordlist_supplied": True})
    source = job_dir / 'input' / 'protected.xlsx'
    source.write_bytes(OFFICE_FIXTURE.read_bytes())
    wordlist = job_dir / 'input' / 'custom-wordlist.txt'
    wordlist.write_text(f'{TEST_PASSWORD}\nnot-it\n', encoding='utf-8')
    store.update(
        job_id,
        source_relative='input/protected.xlsx',
        source_size=source.stat().st_size,
        custom_wordlist_path=str(wordlist),
    )

    asyncio.run(run_pipeline(job_id, settings=settings, store=store, runner=runner))

    state = store.get(job_id)
    assert state['status'] == 'completed', state
    assert state['artifact_ready'] is True
    assert state['report_ready'] is True
    assert state['stage'] == 'completed'
    assert all('password' not in key for key in state)
    assert TEST_PASSWORD not in json.dumps(state, ensure_ascii=False)

    extract_hash = (job_dir / 'work' / 'extract-hash.stdout.txt').read_text(encoding='utf-8', errors='replace')
    assert '$office$' in extract_hash

    report_text = (job_dir / 'report.json').read_text(encoding='utf-8')
    assert TEST_PASSWORD not in report_text

    for name in ['john.stdout.txt', 'john.stderr.txt', 'extract-hash.stdout.txt', 'extract-hash.stderr.txt']:
        text = (job_dir / 'work' / name).read_text(encoding='utf-8', errors='replace')
        assert TEST_PASSWORD not in text

    with TestClient(app) as client:
        report = client.get(f'/api/jobs/{job_id}/report')
        assert report.status_code == 200
        assert TEST_PASSWORD not in report.text

        artifact = client.get(f'/api/jobs/{job_id}/artifact')
        assert artifact.status_code == 200
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
            assert 'xl/workbook.xml' in archive.namelist()

        delete_response = client.delete(f'/api/jobs/{job_id}')
        assert delete_response.status_code == 204

    assert not job_dir.exists()

    after_leftovers = {p.name for p in Path('/tmp').glob('john-home-*')}
    assert after_leftovers == before_leftovers
