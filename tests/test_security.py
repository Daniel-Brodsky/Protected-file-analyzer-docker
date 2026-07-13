from pathlib import Path

import pytest

from protected_file_analyzer.security import ensure_within, validate_extension, validate_job_id


def test_supported_extension_is_normalized():
    assert validate_extension("SAMPLE.PDF") == ".pdf"


def test_unsupported_extension_is_rejected():
    with pytest.raises(ValueError):
        validate_extension("sample.exe")


def test_job_id_validation():
    assert validate_job_id("a" * 32) == "a" * 32
    with pytest.raises(ValueError):
        validate_job_id("../../etc/passwd")


def test_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        ensure_within(tmp_path / "safe", tmp_path / "outside")
