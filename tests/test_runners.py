from __future__ import annotations

import asyncio

import httpx

from protected_file_analyzer.config import get_settings
from protected_file_analyzer.runners import KaliMcpRunner, LocalContainerRunner


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"success": True, "stdout": '{"ok": true, "found": false, "timed_out": true}\n'}


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get('timeout')
        _FakeAsyncClient.last_timeout = self.timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json):
        return _FakeResponse()

    async def get(self, url):
        return _FakeResponse()


_FakeAsyncClient.last_timeout = None


def test_kali_runner_uses_extended_read_timeout(app_env, monkeypatch):
    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    settings = get_settings()

    result = asyncio.run(KaliMcpRunner(settings).run_worker(['crack-israeli-id'], timeout=145))

    assert result.payload['timed_out'] is True
    timeout = _FakeAsyncClient.last_timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read is not None and timeout.read >= 240
    assert timeout.connect == 10


def test_local_runner_reports_basic_capabilities(app_env):
    settings = get_settings()
    capabilities = LocalContainerRunner(settings).capabilities()

    assert capabilities['backend'] == 'local'
    assert 'formats' in capabilities
    assert 'scanners' in capabilities
