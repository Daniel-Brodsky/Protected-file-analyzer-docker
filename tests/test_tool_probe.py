from __future__ import annotations

from pathlib import Path

from protected_file_analyzer.tool_probe import create_probe_fixtures, verify_local_toolchain


def test_probe_fixture_generation(tmp_path: Path):
    fixtures = create_probe_fixtures(tmp_path)
    assert fixtures.zip_file.exists()
    assert fixtures.pdf_file.exists()
    assert fixtures.office_file.exists()
    assert fixtures.seven_zip_file.exists()
    assert fixtures.office_file.read_bytes() != (tmp_path / 'sample.xlsx').read_bytes()


def test_local_toolchain_reports_command_paths():
    caps = verify_local_toolchain()
    assert 'command_paths' in caps
    assert 'john' in caps['extractors']
    assert caps['command_paths']['john']
