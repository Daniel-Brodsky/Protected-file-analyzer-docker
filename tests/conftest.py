from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_root = tmp_path / "jobs"
    wordlists = tmp_path / "wordlists"
    rules = tmp_path / "rules"
    wordlists.mkdir(parents=True, exist_ok=True)
    rules.mkdir(parents=True, exist_ok=True)
    (wordlists / "rockyou.txt").write_text("1234\npassword\n", encoding="utf-8")
    (rules / "basic_static.yar").write_text("rule always_true { condition: true }\n", encoding="utf-8")

    monkeypatch.setenv("PFA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("PFA_WORDLISTS_DIR", str(wordlists))
    monkeypatch.setenv("PFA_DEFAULT_ROCKYOU_PATH", str(wordlists / "rockyou.txt"))
    monkeypatch.setenv("PFA_YARA_RULES_PATH", str(rules / "basic_static.yar"))
    monkeypatch.setenv("PFA_TOOL_RUNNER_BACKEND", "local")
    monkeypatch.setenv("PFA_CLAMAV_ENABLED", "0")
    monkeypatch.setenv("PFA_SECRET_KEY", "test-secret-key")

    modules = [
        "protected_file_analyzer.config",
        "protected_file_analyzer.store",
        "protected_file_analyzer.tool_worker",
        "protected_file_analyzer.tool_probe",
        "protected_file_analyzer.runners",
        "protected_file_analyzer.pipeline",
        "protected_file_analyzer.app",
        "protected_file_analyzer.worker_daemon",
    ]
    for name in modules:
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    return {
        "data_root": data_root,
        "wordlists_dir": wordlists,
        "rules_path": rules / "basic_static.yar",
    }
