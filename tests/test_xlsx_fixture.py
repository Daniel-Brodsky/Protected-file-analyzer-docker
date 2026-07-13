from __future__ import annotations

import zipfile
from pathlib import Path

from protected_file_analyzer.tool_worker import crack, decrypt_file, extract_hash, recover_secret


OFFICE_FIXTURE = Path(__file__).with_name('fixtures') / 'protected-sample.xlsx'
PASSWORD = 'ProbePass123!'


def test_blank_wordlist_candidates_are_skipped_without_invoking_john(tmp_path: Path):
    hash_file = tmp_path / 'hash.txt'
    hash_file.write_text('fake-hash\n', encoding='utf-8')
    pot_file = tmp_path / 'john.pot'
    workdir = tmp_path / 'work'
    wordlist = tmp_path / 'blank.txt'
    wordlist.write_text('\n\n   \n', encoding='utf-8')

    cracked = crack(hash_file, wordlist, pot_file, workdir, timeout=30, max_candidates=100, cancel_path=None, provider_name='custom_upload')

    assert cracked == {"ok": True, "found": False, "timed_out": False, "cancelled": False}


def test_office_fixture_extracts_cracks_and_decrypts(tmp_path: Path):
    assert OFFICE_FIXTURE.exists()

    workdir = tmp_path / 'work'
    output = tmp_path / 'output'
    source = tmp_path / 'protected.xlsx'
    source.write_bytes(OFFICE_FIXTURE.read_bytes())

    hash_file = workdir / 'hash.txt'
    pot_file = workdir / 'john.pot'
    secret_file = workdir / 'password.secret'
    wordlist = tmp_path / 'wordlist.txt'
    wordlist.write_text(f'{PASSWORD}\nnot-it\n', encoding='utf-8')

    extracted = extract_hash(source, hash_file)
    assert extracted['ok'] is True
    assert '$office$' in hash_file.read_text(encoding='utf-8')

    cracked = crack(hash_file, wordlist, pot_file, workdir, timeout=30, max_candidates=100, cancel_path=None, provider_name='custom_upload')
    assert cracked['ok'] is True
    assert cracked['found'] is True

    recovered = recover_secret(hash_file, pot_file, secret_file, workdir)
    assert recovered['ok'] is True
    assert secret_file.read_text(encoding='utf-8') == PASSWORD

    decrypted = decrypt_file(source, secret_file, output, 5000, 500 * 1024 * 1024)
    assert decrypted['ok'] is True
    target = Path(decrypted['scan_target'])
    assert target.exists()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    assert 'xl/workbook.xml' in names
    assert 'xl/worksheets/sheet1.xml' in names
