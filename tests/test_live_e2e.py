from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from protected_file_analyzer.tool_probe import TEST_PASSWORD, create_probe_fixtures


BASE_URL = os.getenv('PFA_LIVE_BASE_URL')
REPO_DIR = Path(os.getenv('PFA_REPO_DIR') or str(Path(__file__).resolve().parents[1]))
OFFICE_FIXTURE = REPO_DIR / 'tests' / 'fixtures' / 'protected-sample.xlsx'

pytestmark = pytest.mark.skipif(not BASE_URL, reason='PFA_LIVE_BASE_URL not set for live end-to-end tests')


def _grep_clean(command: str, needle: str) -> None:
    output = subprocess.run(command, shell=True, text=True, capture_output=True, cwd=REPO_DIR, check=False).stdout
    assert needle not in output


def _assert_no_password_leak(password: str) -> None:
    _grep_clean("docker compose logs --no-color web worker 2>/dev/null || true", password)
    _grep_clean("docker exec protected-file-analyzer-worker-1 ps -ef 2>/dev/null || true", password)
    _grep_clean("docker exec protected-file-analyzer-web-1 ps -ef 2>/dev/null || true", password)
    _grep_clean("docker inspect protected-file-analyzer-web-1 protected-file-analyzer-worker-1 2>/dev/null || true", password)


def _build_supported_7z(path: Path, password: str) -> None:
    seven_zip_bin = shutil.which('7z') or shutil.which('7zz') or shutil.which('7za')
    plain = path.with_suffix('.txt')
    plain.write_text('hello from live e2e\n', encoding='utf-8')
    if seven_zip_bin:
        subprocess.run([seven_zip_bin, 'a', '-t7z', '-m0=Copy', f'-p{password}', str(path), str(plain)], check=True, cwd=path.parent, capture_output=True, text=True)
        return
    subprocess.run(
        [
            'docker', 'run', '--rm',
            '-v', f'{path.parent}:/work',
            'kalilinux/kali-rolling', 'bash', '-lc',
            f'apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends p7zip-full >/dev/null && 7z a -t7z -m0=Copy -p{password} /work/{path.name} /work/{plain.name} >/dev/null'
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _run_live_job(client: httpx.Client, fixture_path: Path, *, suffix_name: str) -> None:
    with fixture_path.open('rb') as protected_handle:
        wordlist_bytes = f"{TEST_PASSWORD}\nnot-it\n".encode('utf-8')
        response = client.post(
            '/api/jobs',
            data={'wordlist_mode': 'custom', 'authorization_confirmed': 'true'},
            files={
                'protected_file': (suffix_name, protected_handle, 'application/octet-stream'),
                'custom_wordlist': ('wordlist.txt', wordlist_bytes, 'text/plain'),
            },
        )
    response.raise_for_status()
    job_id = response.json()['job_id']

    terminal = None
    while True:
        _assert_no_password_leak(TEST_PASSWORD)
        state = client.get(f'/api/jobs/{job_id}').json()
        terminal = state
        if state['status'] in {'completed', 'failed', 'cancelled', 'not_cracked', 'timed_out'}:
            break
        time.sleep(1)

    assert terminal is not None
    assert terminal['status'] == 'completed', terminal
    assert TEST_PASSWORD not in str(terminal)

    report = client.get(f'/api/jobs/{job_id}/report')
    report.raise_for_status()
    assert TEST_PASSWORD not in report.text

    artifact = client.get(f'/api/jobs/{job_id}/artifact')
    artifact.raise_for_status()
    assert artifact.content

    reveal = client.post(f'/api/jobs/{job_id}/reveal-password')
    reveal.raise_for_status()
    assert reveal.json()['password'] == TEST_PASSWORD
    assert 'no-store' in reveal.headers.get('cache-control', '')

    _assert_no_password_leak(TEST_PASSWORD)

    delete_response = client.delete(f'/api/jobs/{job_id}')
    assert delete_response.status_code == 204


def test_live_end_to_end_supported_formats(tmp_path: Path):
    fixtures = create_probe_fixtures(tmp_path)
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        capabilities = client.get('/api/capabilities')
        capabilities.raise_for_status()
        caps = capabilities.json()

        if caps['extractors'].get('zip2john'):
            _run_live_job(client, fixtures.zip_file, suffix_name='sample.zip')
        if caps['extractors'].get('pdf2john'):
            _run_live_job(client, fixtures.pdf_file, suffix_name='sample.pdf')
        if caps['extractors'].get('office2john'):
            assert OFFICE_FIXTURE.exists()
            _run_live_job(client, OFFICE_FIXTURE, suffix_name='sample.xlsx')
        if caps['extractors'].get('7z2john'):
            _build_supported_7z(fixtures.seven_zip_file, TEST_PASSWORD)
            _run_live_job(client, fixtures.seven_zip_file, suffix_name='sample.7z')
