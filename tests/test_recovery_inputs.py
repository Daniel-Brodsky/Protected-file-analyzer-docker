from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from protected_file_analyzer.config import get_settings
from protected_file_analyzer.recovery import build_recovery_plan
from protected_file_analyzer.tool_worker import crack_scoped_id_patterns, record_wordlist_metadata, write_pin_candidates


def _reload_recovery_modules():
    for name in ['protected_file_analyzer.config', 'protected_file_analyzer.recovery']:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def test_no_rockyou_no_mounted_no_custom_upload_results_in_pin_only_plan(monkeypatch, tmp_path: Path):
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
    _reload_recovery_modules()

    settings = get_settings()
    plan = build_recovery_plan(settings)

    assert [attempt.source for attempt in plan] == ['pin4']
    assert plan[0].max_candidates == 10000


def test_pin_candidate_generation_always_produces_10000_candidates(tmp_path: Path):
    candidate_file = tmp_path / 'pin4.txt'

    count = write_pin_candidates(candidate_file, 10000)

    lines = candidate_file.read_text(encoding='utf-8').splitlines()
    assert count == 10000
    assert len(lines) == 10000
    assert lines[0] == '0000'
    assert lines[-1] == '9999'


def test_empty_scoped_provider_is_skipped_without_invoking_john(tmp_path: Path):
    hash_file = tmp_path / 'hash.txt'
    hash_file.write_text('fake-hash\n', encoding='utf-8')
    pot_file = tmp_path / 'john.pot'
    workdir = tmp_path / 'work'

    result = crack_scoped_id_patterns(
        hash_file=hash_file,
        pot=pot_file,
        workdir=workdir,
        timeout=30,
        max_candidates=100,
        prefixes=[],
        cancel_path=None,
        provider_name='scoped_org_patterns',
    )

    assert result == {"ok": True, "found": False, "timed_out": False, "cancelled": False}


def test_wordlist_metadata_records_safe_fields_only(tmp_path: Path):
    workdir = tmp_path / 'work'
    workdir.mkdir()
    wordlist = workdir / 'candidates.txt'
    wordlist.write_text('1111\n2222\n', encoding='utf-8')

    record_wordlist_metadata(workdir=workdir, provider_name='pin4', wordlist=wordlist)

    payload = json.loads((workdir / 'pin4-wordlist-meta.json').read_text(encoding='utf-8'))
    assert payload['provider'] == 'pin4'
    assert payload['exists'] is True
    assert payload['regular_file'] is True
    assert payload['readable'] is True
    assert payload['byte_size'] > 0
    assert payload['non_empty_line_count'] == 2
    assert payload['flushed_and_closed_before_john'] is True
    assert '1111' not in json.dumps(payload)
    assert '2222' not in json.dumps(payload)
