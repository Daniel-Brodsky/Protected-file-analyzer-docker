from __future__ import annotations

import importlib
import sys
from pathlib import Path

from protected_file_analyzer.config import get_settings
from protected_file_analyzer.recovery import build_recovery_plan
from protected_file_analyzer.runners import LocalContainerRunner


def _reload_modules():
    for name in [
        'protected_file_analyzer.config',
        'protected_file_analyzer.recovery',
        'protected_file_analyzer.runners',
        'protected_file_analyzer.app',
    ]:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def test_capabilities_report_rockyou_present(app_env):
    settings = get_settings()
    caps = LocalContainerRunner(settings).capabilities()

    assert caps['wordlists']['rockyou'] is True


def test_capabilities_report_rockyou_absent_but_application_can_start(monkeypatch, tmp_path: Path):
    data_root = tmp_path / 'jobs'
    wordlists = tmp_path / 'wordlists'
    rules = tmp_path / 'rules'
    data_root.mkdir(parents=True)
    wordlists.mkdir(parents=True)
    rules.mkdir(parents=True)
    (rules / 'basic_static.yar').write_text('rule always_true { condition: true }\n', encoding='utf-8')

    monkeypatch.setenv('PFA_DATA_ROOT', str(data_root))
    monkeypatch.setenv('PFA_WORDLISTS_DIR', str(wordlists))
    monkeypatch.setenv('PFA_DEFAULT_ROCKYOU_PATH', str(wordlists / 'missing-rockyou.txt'))
    monkeypatch.setenv('PFA_YARA_RULES_PATH', str(rules / 'basic_static.yar'))
    monkeypatch.setenv('PFA_TOOL_RUNNER_BACKEND', 'local')
    monkeypatch.setenv('PFA_CLAMAV_ENABLED', '0')
    monkeypatch.setenv('PFA_SECRET_KEY', 'test-secret-key')
    _reload_modules()

    from protected_file_analyzer.app import create_app

    settings = get_settings()
    caps = LocalContainerRunner(settings).capabilities()
    app = create_app(settings=settings)

    assert caps['wordlists']['rockyou'] is False
    assert app.title == 'Protected File Analyzer'


def test_recovery_plan_uses_recommended_default_order_and_disables_scoped_patterns_by_default(app_env):
    settings = get_settings()
    mounted_wordlist = settings.wordlists_dir / 'org.txt'
    mounted_wordlist.write_text('org-guess\n', encoding='utf-8')
    custom_wordlist = settings.data_root / 'custom.txt'
    custom_wordlist.write_text('custom\n', encoding='utf-8')

    plan = build_recovery_plan(settings, custom_wordlist_path=custom_wordlist)

    assert [attempt.source for attempt in plan] == ['custom_upload', 'mounted_wordlists', 'pin4', 'rockyou']
    assert all(attempt.source != 'scoped_org_patterns' for attempt in plan)
    assert plan[0].max_candidates == settings.recovery_custom_max_candidates
    assert plan[1].max_candidates == settings.recovery_mounted_max_candidates
    assert plan[2].max_candidates == settings.recovery_pin_max_candidates
    assert plan[3].max_candidates == settings.recovery_rockyou_max_candidates


def test_recovery_plan_enables_scoped_patterns_only_when_explicitly_configured(monkeypatch, tmp_path: Path):
    data_root = tmp_path / 'jobs'
    wordlists = tmp_path / 'wordlists'
    rules = tmp_path / 'rules'
    data_root.mkdir(parents=True)
    wordlists.mkdir(parents=True)
    rules.mkdir(parents=True)
    (wordlists / 'rockyou.txt').write_text('rockyou\n', encoding='utf-8')
    (rules / 'basic_static.yar').write_text('rule always_true { condition: true }\n', encoding='utf-8')

    monkeypatch.setenv('PFA_DATA_ROOT', str(data_root))
    monkeypatch.setenv('PFA_WORDLISTS_DIR', str(wordlists))
    monkeypatch.setenv('PFA_DEFAULT_ROCKYOU_PATH', str(wordlists / 'rockyou.txt'))
    monkeypatch.setenv('PFA_YARA_RULES_PATH', str(rules / 'basic_static.yar'))
    monkeypatch.setenv('PFA_SCOPED_ORG_PATTERNS_ENABLED', '1')
    monkeypatch.setenv('PFA_SCOPED_ORG_ID_PREFIXES', '1234,5678')
    monkeypatch.setenv('PFA_SCOPED_ORG_PATTERN_MAX_CANDIDATES', '1200')
    _reload_modules()

    settings = get_settings()
    plan = build_recovery_plan(settings)
    scoped = plan[-1]

    assert scoped.source == 'scoped_org_patterns'
    assert scoped.max_candidates == 1200
    assert scoped.prefixes == ('1234', '5678')
