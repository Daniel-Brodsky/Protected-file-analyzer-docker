from __future__ import annotations

from pathlib import Path

import pytest

from protected_file_analyzer.config import get_settings
from protected_file_analyzer.worker_daemon import ensure_runtime_writable


def test_ensure_runtime_writable_accepts_normal_temp_dirs(app_env):
    settings = get_settings()
    ensure_runtime_writable(settings)


def test_ensure_runtime_writable_raises_clear_error_for_read_only_dir(app_env, monkeypatch, tmp_path: Path):
    data_root = tmp_path / 'jobs-read-only'
    wordlists = tmp_path / 'wordlists'
    data_root.mkdir(parents=True, exist_ok=True)
    wordlists.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('PFA_DATA_ROOT', str(data_root))
    monkeypatch.setenv('PFA_WORDLISTS_DIR', str(wordlists))

    from protected_file_analyzer.config import get_settings as fresh_settings
    fresh_settings.cache_clear()
    settings = fresh_settings()
    expected_data_root = settings.data_root

    original_assert = ensure_runtime_writable.__globals__['_assert_directory_writable']

    def fake_assert(path: Path, label: str) -> None:
        if path == expected_data_root:
            raise RuntimeError(f'{label} is not writable: {path}. Prepare runtime directories with the setup/start flow before running Compose directly.')
        return original_assert(path, label)

    monkeypatch.setitem(ensure_runtime_writable.__globals__, '_assert_directory_writable', fake_assert)

    with pytest.raises(RuntimeError, match='PFA data root is not writable'):
        ensure_runtime_writable(settings)
