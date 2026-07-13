from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf
import py7zr
import pyzipper
import xlsxwriter
from msoffcrypto.format.ooxml import OOXMLFile

from . import tool_worker


# Non-sensitive fixed value used only for generated probe fixtures and tests.
TEST_PASSWORD = "ProbePass123!"


@dataclass
class ProbeFixtureSet:
    root: Path
    zip_file: Path
    pdf_file: Path
    office_file: Path
    seven_zip_file: Path


def create_probe_fixtures(root: Path, password: str = TEST_PASSWORD, *, prefer_cli_7z: bool = False) -> ProbeFixtureSet:
    root.mkdir(parents=True, exist_ok=True)
    plaintext = root / "sample.txt"
    plaintext.write_text("hello from protected-file-analyzer\n", encoding="utf-8")

    zip_file = root / "sample.zip"
    with pyzipper.AESZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.writestr("sample.txt", plaintext.read_bytes())

    pdf_file = root / "sample.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(
        pdf_file,
        encryption=pikepdf.Encryption(owner=password, user=password, R=4),
    )

    office_plain = root / "sample.xlsx"
    _write_minimal_xlsx(office_plain)
    office_file = root / "sample-encrypted.xlsx"
    with office_plain.open("rb") as infile, office_file.open("wb") as outfile:
        OOXMLFile(infile).encrypt(password, outfile)

    seven_zip_file = root / "sample.7z"
    _write_seven_zip(seven_zip_file, plaintext, password, prefer_cli=prefer_cli_7z)

    return ProbeFixtureSet(root=root, zip_file=zip_file, pdf_file=pdf_file, office_file=office_file, seven_zip_file=seven_zip_file)


def verify_local_toolchain() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pfa-probe-") as temp_dir:
        fixtures = create_probe_fixtures(Path(temp_dir), prefer_cli_7z=True)

        john_path = _tool_path("john")
        zip2john_path = _tool_path("zip2john")
        seven2john_path = _tool_path("7z2john", "7z2john.pl")
        pdf2john_path = _tool_path("pdf2john", "pdf2john.py", "pdf2john.pl")
        office2john_path = _tool_path("office2john", "office2john.py")
        yara_path = shutil.which("yara")
        oleid_path = shutil.which("oleid")
        olevba_path = shutil.which("olevba")
        pdfid_path = shutil.which("pdfid") or shutil.which("pdfid.py")
        exiftool_path = shutil.which("exiftool")
        extractors = {
            "john": _run_success([str(john_path), "--list=formats"])[0],
            "zip2john": _run_nonempty(tool_worker.script_command(zip2john_path, str(fixtures.zip_file)))[0],
            "7z2john": _run_nonempty(tool_worker.script_command(seven2john_path, str(fixtures.seven_zip_file)))[0],
            "pdf2john": _run_nonempty(tool_worker.script_command(pdf2john_path, str(fixtures.pdf_file)))[0],
            "office2john": _run_nonempty(tool_worker.script_command(office2john_path, str(fixtures.office_file)))[0],
        }
        scanners = {
            "yara": _run_success([yara_path, "--version"])[0] if yara_path else False,
            "oleid": _run_nonempty([oleid_path, str(fixtures.office_file)])[0] if oleid_path else False,
            "olevba": _run_success([olevba_path, "--help"])[0] if olevba_path else False,
            "pdfid": _run_nonempty([pdfid_path, str(fixtures.pdf_file)])[0] if pdfid_path else False,
            "exiftool": _run_nonempty([exiftool_path, str(fixtures.pdf_file)])[0] if exiftool_path else False,
        }
        return {
            "extractors": extractors,
            "scanners": scanners,
            "command_paths": {
                "john": str(john_path),
                "zip2john": str(zip2john_path),
                "7z2john": str(seven2john_path),
                "pdf2john": str(pdf2john_path),
                "office2john": str(office2john_path),
                "yara": yara_path,
                "oleid": oleid_path,
                "olevba": olevba_path,
                "pdfid": pdfid_path,
                "exiftool": exiftool_path,
            },
        }


def _tool_path(*names: str) -> Path:
    return tool_worker.find_tool(*names)


def _run_success(command: list[str], timeout: int = 60) -> tuple[bool, str]:
    with tool_worker.temporary_john_environment("probe") as env:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output


def _run_nonempty(command: list[str], timeout: int = 60) -> tuple[bool, str]:
    with tool_worker.temporary_john_environment("probe") as env:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    output = (result.stdout or "")
    return result.returncode == 0 and bool(output.strip()), output + (result.stderr or "")


def _write_seven_zip(path: Path, plaintext: Path, password: str, *, prefer_cli: bool) -> None:
    seven_zip_bin = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if prefer_cli and seven_zip_bin:
        result = subprocess.run(
            [seven_zip_bin, "a", "-t7z", "-m0=Copy", f"-p{password}", str(path), str(plaintext)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"7z fixture generation failed: {(result.stderr or result.stdout).strip()}")
        return
    with py7zr.SevenZipFile(path, "w", password=password) as archive:
        archive.write(plaintext, arcname=plaintext.name)


def _write_minimal_xlsx(path: Path) -> None:
    workbook = xlsxwriter.Workbook(str(path))
    worksheet = workbook.add_worksheet('Sheet1')
    worksheet.write('A1', 'probe')
    workbook.close()
