#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import msoffcrypto
import pikepdf
import py7zr
import pyzipper

try:
    import magic
except ImportError:  # pragma: no cover
    magic = None

OFFICE_EXTENSIONS = {".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".pptm"}


class WorkerError(RuntimeError):
    pass


def emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    raise SystemExit(exit_code)


def find_tool(*names: str) -> Path:
    candidates: list[Path] = []
    for name in names:
        located = shutil.which(name)
        if located:
            candidates.append(Path(located))
        candidates.extend([
            Path("/usr/share/john") / name,
            Path("/usr/lib/john") / name,
            Path("/opt/john/run") / name,
        ])
    for candidate in candidates:
        if not candidate.exists() or not os.access(candidate, os.R_OK):
            continue
        if candidate.suffix in {".py", ".pl"} or os.access(candidate, os.X_OK):
            return candidate
    raise WorkerError(f"Required tool was not found: {', '.join(names)}")


def john_scope_from_path(path: Path | None) -> str:
    if path is None:
        return "job"
    if path.name in {"work", "input", "output"} and path.parent.name:
        return path.parent.name
    return path.name or "job"


@contextmanager
def temporary_john_environment(scope: str | None = None) -> Iterator[dict[str, str]]:
    suffix = re.sub(r"[^A-Za-z0-9_.-]", "-", (scope or "")).strip("-")
    prefix = "john-home-" if not suffix else f"john-home-{suffix}-"
    home = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
    try:
        subprocess.run(["install", "-d", "-m", "0700", str(home), str(home / ".john")], check=True)
        env = os.environ.copy()
        env["HOME"] = str(home)
        yield env
    finally:
        shutil.rmtree(home, ignore_errors=True)


def run(args: list[str], *, timeout: int = 120, cwd: Path | None = None,
        quiet: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        timeout=timeout,
        text=True,
        stdout=subprocess.DEVNULL if quiet else subprocess.PIPE,
        stderr=subprocess.DEVNULL if quiet else subprocess.PIPE,
        check=False,
    )


def script_command(tool: Path, *args: str) -> list[str]:
    if tool.suffix in {".py", ".pl"}:
        interpreter = sys.executable if tool.suffix == ".py" else "perl"
        return [interpreter, str(tool), *args]
    return [str(tool), *args]


def john_timed_out(workdir: Path) -> bool:
    log_path = workdir / "john-session.log"
    if not log_path.exists():
        return False
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "Session stopped (max run-time reached)" in log_text


def clear_john_session(workdir: Path) -> None:
    for item in workdir.glob("john-session*"):
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


def strip_ansi_and_control_sequences(text: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", text or "")
    return "".join(char for char in text if char in {"\n", "\r", "\t"} or ord(char) >= 32)


def truncate_text_bytes(text: str, limit: int) -> tuple[str, bool]:
    data = (text or "").encode("utf-8")
    if len(data) <= limit:
        return text or "", False
    return data[:limit].decode("utf-8", errors="ignore"), True


def slugify_fragment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-.")
    return cleaned.lower() or "tool-output"


def count_non_empty_lines(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def record_wordlist_metadata(*, workdir: Path, provider_name: str, wordlist: Path, skipped_empty: bool = False) -> None:
    exists = wordlist.exists()
    stat_result = wordlist.stat() if exists else None
    payload = {
        "provider": provider_name,
        "resolved_container_path": str(wordlist.resolve()),
        "exists": exists,
        "regular_file": bool(exists and wordlist.is_file()),
        "readable": bool(exists and os.access(wordlist, os.R_OK)),
        "byte_size": int(stat_result.st_size) if stat_result else 0,
        "non_empty_line_count": count_non_empty_lines(wordlist) if exists else 0,
        "ownership": {
            "uid": int(stat_result.st_uid) if stat_result else None,
            "gid": int(stat_result.st_gid) if stat_result else None,
        },
        "mode": oct(stat_result.st_mode & 0o777) if stat_result else None,
        "flushed_and_closed_before_john": True,
        "skipped_empty": skipped_empty,
    }
    metadata_path = workdir / f"{slugify_fragment(provider_name)}-wordlist-meta.json"
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def terminate_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        process.terminate()
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


def run_cancellable(
    args: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    cancel_path: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str] | None, bool, bool]:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + timeout
    while True:
        if cancel_path is not None and cancel_path.exists():
            stdout, stderr = terminate_process(process)
            return subprocess.CompletedProcess(args, process.returncode or 130, stdout, stderr), False, True
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            return subprocess.CompletedProcess(args, process.returncode or 0, stdout, stderr), False, False
        if time.monotonic() >= deadline:
            stdout, stderr = terminate_process(process)
            return subprocess.CompletedProcess(args, process.returncode or 124, stdout, stderr), True, False
        time.sleep(0.2)


def limit_wordlist(source: Path, destination: Path, max_candidates: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with source.open("r", encoding="utf-8", errors="ignore") as src, destination.open("w", encoding="utf-8") as dst:
        for line in src:
            candidate = line.rstrip("\r\n")
            if not candidate.strip():
                continue
            if count >= max_candidates:
                break
            dst.write(candidate + "\n")
            count += 1
    return count


def write_pin_candidates(destination: Path, max_candidates: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    upper = min(max_candidates, 10_000)
    with destination.open("w", encoding="utf-8") as handle:
        for value in range(upper):
            handle.write(f"{value:04d}\n")
            count += 1
    return count


def write_scoped_israeli_id_candidates(destination: Path, prefixes: list[str], max_candidates: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    written = 0
    with destination.open("w", encoding="utf-8") as handle:
        for prefix in prefixes:
            prefix = prefix.strip()
            if not prefix or not prefix.isdigit() or len(prefix) > 8:
                continue
            suffix_width = 8 - len(prefix)
            suffix_limit = 10 ** suffix_width
            for suffix in range(suffix_limit):
                if written >= max_candidates:
                    return written
                first_eight = f"{prefix}{suffix:0{suffix_width}d}"
                candidate = first_eight + israeli_id_check_digit(first_eight)
                if candidate in seen:
                    continue
                seen.add(candidate)
                handle.write(candidate + "\n")
                written += 1
    return written


def run_john_with_wordlist(
    *,
    hash_file: Path,
    wordlist: Path,
    pot: Path,
    workdir: Path,
    timeout: int,
    cancel_path: Path | None,
    provider_name: str,
) -> dict[str, Any]:
    john = find_tool("john")
    workdir.mkdir(parents=True, exist_ok=True)
    pot.parent.mkdir(parents=True, exist_ok=True)
    clear_john_session(workdir)
    command = [
        str(john),
        f"--wordlist={wordlist}",
        f"--pot={pot}",
        f"--session={workdir / 'john-session'}",
        f"--max-run-time={timeout}",
        "--verbosity=1",
        str(hash_file),
    ]
    record_wordlist_metadata(workdir=workdir, provider_name=provider_name, wordlist=wordlist)
    with temporary_john_environment(john_scope_from_path(workdir)) as env:
        result, timed_out, cancelled = run_cancellable(command, timeout=timeout + 15, cwd=workdir, env=env, cancel_path=cancel_path)
    found = pot.exists() and pot.stat().st_size > 0
    if result is not None:
        raise_for_john_failure(workdir, result, found=found)
    return {"ok": True, "found": found, "timed_out": (not found) and timed_out, "cancelled": cancelled}


JOHN_FATAL_MARKERS = (
    "no password hashes loaded",
    "unknown ciphertext format",
    "failed to open",
    "can't open",
    "cannot open",
    "no such file or directory",
    "unknown option",
)


def persist_john_output(workdir: Path, stdout: str, stderr: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "john.stdout.txt").write_text(stdout or "", encoding="utf-8")
    (workdir / "john.stderr.txt").write_text(stderr or "", encoding="utf-8")


def persist_redacted_john_success(workdir: Path) -> None:
    persist_john_output(
        workdir,
        "[REDACTED: successful crack output suppressed]\n",
        "[REDACTED: successful crack output suppressed]\n",
    )



def john_fast_fail_message(stdout: str, stderr: str) -> str | None:
    lines = [line.strip() for line in (stdout or "").splitlines() + (stderr or "").splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in JOHN_FATAL_MARKERS):
            return line
    return None



def john_last_output_line(stdout: str, stderr: str) -> str:
    lines = [line.strip() for line in (stdout or "").splitlines() + (stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else "John exited without a usable result"



def raise_for_john_failure(workdir: Path, result: subprocess.CompletedProcess[str], *, found: bool = False) -> None:
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    fast_fail = john_fast_fail_message(stdout, stderr)
    if fast_fail:
        persist_john_output(workdir, stdout, stderr)
        raise WorkerError(f"John failed early: {fast_fail}")
    if result.returncode != 0:
        persist_john_output(workdir, stdout, stderr)
        raise WorkerError(f"John exited with code {result.returncode}: {john_last_output_line(stdout, stderr)}")
    if found:
        persist_redacted_john_success(workdir)
    else:
        persist_john_output(workdir, stdout, stderr)



def persist_extract_hash_output(output: Path, stdout: str, stderr: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    (output.parent / "extract-hash.stdout.txt").write_text(stdout or "", encoding="utf-8")
    (output.parent / "extract-hash.stderr.txt").write_text(stderr or "", encoding="utf-8")



def normalize_pdf_hash_output(stdout: str) -> str:
    if not stdout.strip() or "$pdf$" not in stdout:
        return stdout
    normalized_lines: list[str] = []
    for line in stdout.splitlines():
        if "$pdf$" not in line:
            normalized_lines.append(line)
            continue
        prefix, payload = line.split("$pdf$", 1)
        fields = payload.split("*")
        if len(fields) >= 4:
            try:
                permissions = int(fields[3])
            except ValueError:
                permissions = None
            if permissions is not None and permissions > 0x7FFFFFFF:
                fields[3] = str(permissions - 0x100000000)
        normalized_lines.append(prefix + "$pdf$" + "*".join(fields))
    suffix = "\n" if stdout.endswith("\n") else ""
    return "\n".join(normalized_lines) + suffix



def extract_hash(source: Path, output: Path) -> dict[str, Any]:
    ext = source.suffix.lower()
    if ext == ".zip":
        tool = find_tool("zip2john")
    elif ext == ".7z":
        tool = find_tool("7z2john", "7z2john.pl")
    elif ext == ".pdf":
        tool = find_tool("pdf2john", "pdf2john.py", "pdf2john.pl")
    elif ext in OFFICE_EXTENSIONS:
        tool = find_tool("office2john", "office2john.py")
    else:
        raise WorkerError("This file type is not supported by the protected-file workflow")

    with temporary_john_environment(john_scope_from_path(output.parent)) as env:
        result = run(script_command(tool, str(source)), timeout=90, env=env)
    stdout = result.stdout or ""
    if ext == ".pdf":
        stdout = normalize_pdf_hash_output(stdout)
    stderr = result.stderr or ""
    persist_extract_hash_output(output, stdout, stderr)
    lowered = stdout.lower()
    if result.returncode == 0 and "not encrypted!" in lowered:
        output.unlink(missing_ok=True)
        persist_extract_hash_output(output, stdout, stderr)
        return {"ok": True, "hash_type": ext.lstrip("."), "not_encrypted": True}
    if ext == ".pdf" and stdout.strip() and "$pdf$" not in stdout:
        persist_extract_hash_output(output, stdout, stderr)
        first_line = stdout.strip().splitlines()[0][:300]
        raise WorkerError(f"Hash extraction produced non-PDF hash output: {first_line}")
    if result.returncode != 0 or not stdout.strip():
        persist_extract_hash_output(output, stdout, stderr)
        detail = (stderr or "No hash was produced").strip().splitlines()[-1]
        raise WorkerError(f"Hash extraction failed: {detail[:300]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stdout, encoding="utf-8")
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR)
    return {"ok": True, "hash_type": ext.lstrip(".")}


def crack(
    hash_file: Path,
    wordlist: Path,
    pot: Path,
    workdir: Path,
    timeout: int,
    max_candidates: int,
    cancel_path: Path | None,
    provider_name: str,
) -> dict[str, Any]:
    limited_wordlist = workdir / "limited-wordlist.txt"
    candidate_count = limit_wordlist(wordlist, limited_wordlist, max_candidates)
    if candidate_count == 0:
        record_wordlist_metadata(workdir=workdir, provider_name=provider_name, wordlist=limited_wordlist, skipped_empty=True)
        limited_wordlist.unlink(missing_ok=True)
        return {"ok": True, "found": False, "timed_out": False, "cancelled": False}
    try:
        return run_john_with_wordlist(
            hash_file=hash_file,
            wordlist=limited_wordlist,
            pot=pot,
            workdir=workdir,
            timeout=timeout,
            cancel_path=cancel_path,
            provider_name=provider_name,
        )
    finally:
        limited_wordlist.unlink(missing_ok=True)



def crack_mask(
    hash_file: Path,
    mask: str,
    pot: Path,
    workdir: Path,
    timeout: int,
    max_candidates: int,
    cancel_path: Path | None,
    provider_name: str,
) -> dict[str, Any]:
    if mask != "?d?d?d?d":
        raise WorkerError("Unsupported mask policy")
    candidate_file = workdir / "pin4-candidates.txt"
    candidate_count = write_pin_candidates(candidate_file, max_candidates)
    if candidate_count == 0:
        record_wordlist_metadata(workdir=workdir, provider_name=provider_name, wordlist=candidate_file, skipped_empty=True)
        candidate_file.unlink(missing_ok=True)
        return {"ok": True, "found": False, "timed_out": False, "cancelled": False}
    try:
        return run_john_with_wordlist(
            hash_file=hash_file,
            wordlist=candidate_file,
            pot=pot,
            workdir=workdir,
            timeout=timeout,
            cancel_path=cancel_path,
            provider_name=provider_name,
        )
    finally:
        candidate_file.unlink(missing_ok=True)

def israeli_id_check_digit(first_eight: str) -> str:
    total = 0
    for index, char in enumerate(first_eight):
        value = int(char) * (1 if index % 2 == 0 else 2)
        total += value if value < 10 else value - 9
    return str((10 - (total % 10)) % 10)


def is_valid_israeli_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d{9}", value)) and israeli_id_check_digit(value[:8]) == value[8]


def crack_scoped_id_patterns(
    hash_file: Path,
    pot: Path,
    workdir: Path,
    timeout: int,
    max_candidates: int,
    prefixes: list[str],
    cancel_path: Path | None,
    provider_name: str,
) -> dict[str, Any]:
    candidate_file = workdir / "scoped-org-patterns.txt"
    candidate_count = write_scoped_israeli_id_candidates(candidate_file, prefixes, max_candidates)
    if candidate_count == 0:
        record_wordlist_metadata(workdir=workdir, provider_name=provider_name, wordlist=candidate_file, skipped_empty=True)
        candidate_file.unlink(missing_ok=True)
        return {"ok": True, "found": False, "timed_out": False, "cancelled": False}
    try:
        return run_john_with_wordlist(
            hash_file=hash_file,
            wordlist=candidate_file,
            pot=pot,
            workdir=workdir,
            timeout=timeout,
            cancel_path=cancel_path,
            provider_name=provider_name,
        )
    finally:
        candidate_file.unlink(missing_ok=True)


def recover_secret(hash_file: Path, pot: Path, output: Path, workdir: Path) -> dict[str, Any]:
    john = find_tool("john")
    with temporary_john_environment(john_scope_from_path(workdir)) as env:
        result = run([
            str(john), "--show", f"--pot={pot}", str(hash_file)
        ], timeout=30, cwd=workdir, env=env)
    password: str | None = None
    for line in result.stdout.splitlines():
        if not line or line.startswith("0 password") or line.startswith("1 password"):
            continue
        parts = line.split(":")
        if len(parts) >= 2 and parts[1]:
            password = parts[1]
            break
    if password is None:
        raise WorkerError("John reported a match but the temporary secret could not be recovered")
    output.write_text(password, encoding="utf-8")
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR)
    # Remove JtR state as soon as the one-time secret exists.
    pot.unlink(missing_ok=True)
    hash_file.unlink(missing_ok=True)
    for item in workdir.glob("john-session*"):
        item.unlink(missing_ok=True)
    return {"ok": True, "secret_ready": True}


def safe_member_path(base: Path, member: str) -> Path:
    member_path = (base / member).resolve()
    base_resolved = base.resolve()
    if member_path != base_resolved and base_resolved not in member_path.parents:
        raise WorkerError(f"Archive member escapes extraction directory: {member}")
    return member_path


def enforce_archive_limits(names_and_sizes: Iterable[tuple[str, int]], base: Path,
                           max_files: int, max_bytes: int) -> None:
    total = 0
    count = 0
    for name, size in names_and_sizes:
        safe_member_path(base, name)
        count += 1
        total += max(size, 0)
        if count > max_files:
            raise WorkerError("Archive contains too many files")
        if total > max_bytes:
            raise WorkerError("Archive expands beyond the configured size limit")


def decrypt_file(source: Path, secret_file: Path, output_dir: Path,
                 max_files: int, max_bytes: int) -> dict[str, Any]:
    password = secret_file.read_text(encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower()
    try:
        if ext == ".zip":
            extracted = output_dir / "extracted"
            extracted.mkdir()
            with pyzipper.AESZipFile(source) as archive:
                infos = archive.infolist()
                for info in infos:
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(unix_mode):
                        raise WorkerError("Symbolic links are not allowed in ZIP content")
                enforce_archive_limits(((i.filename, i.file_size) for i in infos), extracted, max_files, max_bytes)
                archive.extractall(extracted, pwd=password.encode())
            scan_target = extracted
            output_kind = "archive"
        elif ext == ".7z":
            extracted = output_dir / "extracted"
            extracted.mkdir()
            with py7zr.SevenZipFile(source, mode="r", password=password) as archive:
                infos = archive.list()
                enforce_archive_limits(
                    ((info.filename, int(info.uncompressed or 0)) for info in infos if not info.is_directory),
                    extracted, max_files, max_bytes
                )
                archive.extractall(path=extracted)
            validate_extracted_tree(extracted, max_files, max_bytes)
            scan_target = extracted
            output_kind = "archive"
        elif ext == ".pdf":
            target = output_dir / "decrypted.pdf"
            with pikepdf.open(source, password=password) as pdf:
                pdf.save(target)
            scan_target = target
            output_kind = "file"
        elif ext in OFFICE_EXTENSIONS:
            target = output_dir / f"decrypted{ext}"
            with source.open("rb") as infile, target.open("wb") as outfile:
                office = msoffcrypto.OfficeFile(infile)
                office.load_key(password=password)
                office.decrypt(outfile)
            scan_target = target
            output_kind = "file"
        else:
            raise WorkerError("Unsupported decryption format")
    except Exception as exc:
        raise WorkerError(f"Decryption failed: {type(exc).__name__}") from exc
    finally:
        # Do not retain the recovered password, even when decryption fails.
        try:
            password = "\0" * len(password)
        finally:
            secret_file.unlink(missing_ok=True)
    return {"ok": True, "scan_target": str(scan_target), "output_kind": output_kind}


def validate_extracted_tree(root: Path, max_files: int, max_bytes: int) -> None:
    count = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WorkerError("Symbolic links are not allowed in extracted content")
        if path.is_file():
            count += 1
            total += path.stat().st_size
            if count > max_files or total > max_bytes:
                raise WorkerError("Extracted content exceeded configured limits")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mime_type(path: Path) -> str:
    if magic is not None:
        try:
            return str(magic.from_file(str(path), mime=True))
        except Exception:
            pass
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


PDFID_COUNTERS = (
    "/JS",
    "/JavaScript",
    "/AA",
    "/OpenAction",
    "/Launch",
    "/EmbeddedFile",
    "/AcroForm",
    "/XFA",
    "/RichMedia",
    "/ObjStm",
    "/JBIG2Decode",
)


def detect_tool_version(tool_name: str, raw_stdout: str, raw_stderr: str) -> str | None:
    text = f"{raw_stdout}\n{raw_stderr}"
    if tool_name.lower() == "pdfid":
        match = re.search(r"PDFiD\s+([0-9][^\s]*)", text)
        return match.group(1) if match else None
    if tool_name.lower() == "olevba":
        match = re.search(r"olevba(?:\.py)?\s+([0-9][^\s]*)", text, re.IGNORECASE)
        return match.group(1) if match else None
    return None


def store_safe_raw_output(
    *,
    logs_dir: Path,
    tool: str,
    subject: str,
    raw_stdout: str,
    raw_stderr: str,
    ui_limit: int,
    download_limit: int,
) -> dict[str, Any]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    clean_stdout = strip_ansi_and_control_sequences(raw_stdout)
    clean_stderr = strip_ansi_and_control_sequences(raw_stderr)
    display_stdout, stdout_truncated = truncate_text_bytes(clean_stdout, ui_limit)
    display_stderr, stderr_truncated = truncate_text_bytes(clean_stderr, ui_limit)
    combined = clean_stdout or ""
    if clean_stderr:
        combined = f"{combined}\n--- stderr ---\n{clean_stderr}" if combined else f"--- stderr ---\n{clean_stderr}"
    combined_download, download_truncated = truncate_text_bytes(combined, download_limit)
    download_path = None
    if stdout_truncated or stderr_truncated:
        stem = f"{slugify_fragment(tool)}-{slugify_fragment(subject)}-raw"
        path = logs_dir / f"{stem}.txt"
        path.write_text(combined_download, encoding="utf-8")
        download_path = str(path.relative_to(logs_dir.parent))
    return {
        "raw_stdout": display_stdout,
        "raw_stderr": display_stderr,
        "raw_stdout_truncated": stdout_truncated,
        "raw_stderr_truncated": stderr_truncated,
        "raw_output_download": download_path,
        "raw_output_download_truncated": bool(download_path) and download_truncated,
    }


def optional_tool(command: list[str], timeout: int = 90, *, tool_name: str | None = None) -> dict[str, Any]:
    chosen_name = tool_name or Path(command[0]).name
    if not command[0] or not shutil.which(command[0]):
        return {
            "available": False,
            "tool_name": chosen_name,
            "tool_version": None,
            "exit_status": None,
            "raw_stdout": "",
            "raw_stderr": "",
        }
    try:
        result = run(command, timeout=timeout)
        raw_stdout = strip_ansi_and_control_sequences(result.stdout or "")
        raw_stderr = strip_ansi_and_control_sequences(result.stderr or "")
        return {
            "available": True,
            "tool_name": chosen_name,
            "tool_version": detect_tool_version(chosen_name, raw_stdout, raw_stderr),
            "exit_status": result.returncode,
            "raw_stdout": raw_stdout,
            "raw_stderr": raw_stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "tool_name": chosen_name,
            "tool_version": None,
            "exit_status": None,
            "timed_out": True,
            "raw_stdout": "",
            "raw_stderr": "",
        }


def parse_pdfid_output(text: str) -> dict[str, Any]:
    counters: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        for token in PDFID_COUNTERS:
            if stripped.startswith(token):
                parts = stripped.split()
                try:
                    counters[token] = int(parts[-1])
                except (ValueError, IndexError):
                    counters[token] = 0
    return {"counters": {token: counters.get(token, 0) for token in PDFID_COUNTERS}}


def parse_olevba_output(text: str) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        if not columns or all(not value for value in columns):
            continue
        if headers is None and columns[:3] == ["Type", "Keyword", "Description"]:
            headers = columns
            continue
        if headers and columns[0] != "Type" and len(columns) >= len(headers):
            rows.append({headers[index]: columns[index] for index in range(len(headers))})
    return {"rows": rows}


def build_tool_card(*, tool: str, subject: str, result: dict[str, Any], parsed_findings: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_output = store_safe_raw_output(
        logs_dir=result["logs_dir"],
        tool=tool,
        subject=subject,
        raw_stdout=result.get("raw_stdout", ""),
        raw_stderr=result.get("raw_stderr", ""),
        ui_limit=result["tool_output_ui_max_bytes"],
        download_limit=result["tool_output_download_max_bytes"],
    )
    return {
        "tool": tool,
        "subject": subject,
        "available": result.get("available", False),
        "tool_version": result.get("tool_version"),
        "exit_status": result.get("exit_status"),
        **safe_output,
        "parsed_findings": parsed_findings or {},
    }


def scan(target: Path, report_path: Path, artifact_path: Path, rules: Path,
         max_files: int, max_bytes: int) -> dict[str, Any]:
    files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
    if len(files) > max_files:
        raise WorkerError("Too many files to scan")
    total = sum(p.stat().st_size for p in files)
    if total > max_bytes:
        raise WorkerError("Files exceed the static-scan size limit")

    metadata = []
    for path in files[:1000]:
        metadata.append({
            "path": path.name if target.is_file() else str(path.relative_to(target)),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "mime": mime_type(path),
        })

    ui_limit = max(1, int(os.getenv("PFA_TOOL_OUTPUT_UI_MAX_BYTES", "16384")))
    download_limit = max(ui_limit, int(os.getenv("PFA_TOOL_OUTPUT_DOWNLOAD_MAX_BYTES", "262144")))
    logs_dir = report_path.parent / "logs"

    def enrich(result: dict[str, Any]) -> dict[str, Any]:
        return {
            **result,
            "logs_dir": logs_dir,
            "tool_output_ui_max_bytes": ui_limit,
            "tool_output_download_max_bytes": download_limit,
        }

    def public_tool_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in result.items()
            if key not in {"logs_dir", "tool_output_ui_max_bytes", "tool_output_download_max_bytes"}
        }

    scan_subject = str(target)
    yara = enrich({
        "available": False,
        "tool_name": "YARA",
        "tool_version": None,
        "exit_status": None,
        "raw_stdout": "",
        "raw_stderr": "",
    } if not rules.exists() else optional_tool(["yara", "-r", str(rules), scan_subject], timeout=120, tool_name="YARA"))
    exiftool = enrich(optional_tool(["exiftool", "-json", "-n", "-r", scan_subject], timeout=90, tool_name="ExifTool"))

    tool_cards: list[dict[str, Any]] = []
    office_findings: list[dict[str, Any]] = []
    pdf_findings: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() in OFFICE_EXTENSIONS:
            oleid = enrich(optional_tool(["oleid", str(path)], timeout=45, tool_name="oleid"))
            olevba = enrich(optional_tool(["olevba", "--analysis", str(path)], timeout=60, tool_name="olevba"))
            parsed_olevba = parse_olevba_output(olevba.get("raw_stdout", ""))
            office_findings.append({
                "file": path.name,
                "oleid": public_tool_result(oleid),
                "olevba": public_tool_result(olevba),
                "parsed_olevba": parsed_olevba,
            })
            tool_cards.append(build_tool_card(tool="olevba", subject=path.name, result=olevba, parsed_findings=parsed_olevba))
        if path.suffix.lower() == ".pdf":
            pdfid_command = shutil.which("pdfid.py") or shutil.which("pdfid")
            pdfid = enrich(optional_tool([pdfid_command, str(path)], timeout=45, tool_name="PDFiD") if pdfid_command else {
                "available": False,
                "tool_name": "PDFiD",
                "tool_version": None,
                "exit_status": None,
                "raw_stdout": "",
                "raw_stderr": "",
            })
            parsed_pdfid = parse_pdfid_output(pdfid.get("raw_stdout", ""))
            pdf_findings.append({"file": path.name, "pdfid": public_tool_result(pdfid), "parsed_pdfid": parsed_pdfid})
            tool_cards.append(build_tool_card(tool="PDFiD", subject=path.name, result=pdfid, parsed_findings=parsed_pdfid))

    yara_lines = [
        line.strip() for line in f"{yara.get('raw_stdout', '')}\n{yara.get('raw_stderr', '')}".splitlines()
        if line.strip() and not line.lower().startswith(("error", "warning"))
    ]
    yara_hits = bool(yara_lines)
    macro_hits = any(row.get("Type") in {"AutoExec", "Suspicious", "IOC"} for finding in office_findings for row in finding.get("parsed_olevba", {}).get("rows", []))
    pdf_hits = any(any(value > 0 for value in finding.get("parsed_pdfid", {}).get("counters", {}).values()) for finding in pdf_findings)
    indicator_count = sum(1 for flag in (yara_hits, macro_hits, pdf_hits) if flag)
    verdict = "review_recommended" if indicator_count else "no_obvious_findings"

    if target.is_dir():
        artifact_name = "decrypted-analysis-copy.zip"
        temp_zip = artifact_path.with_suffix(".zip")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(target))
        temp_zip.replace(artifact_path)
    else:
        artifact_name = f"decrypted-analysis-copy{target.suffix.lower()}"
        shutil.copy2(target, artifact_path)

    report = {
        "summary": {
            "verdict": verdict,
            "file_count": len(files),
            "total_bytes": total,
            "yara_hits": yara_hits,
            "macro_indicators": macro_hits,
            "pdf_structure_indicators": pdf_hits,
            "indicator_count": indicator_count,
            "note": "Raw tool output is the source of truth. Parsed findings are a navigation layer only.",
        },
        "files": metadata,
        "tool_cards": tool_cards,
        "tools": {
            "yara": public_tool_result(yara),
            "office": office_findings,
            "pdf": pdf_findings,
            "exiftool": public_tool_result(exiftool),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "artifact_name": artifact_name, "summary": report["summary"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fixed-command worker for protected file analysis")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract-hash")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)

    p = sub.add_parser("crack")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--wordlist", required=True, type=Path)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--timeout", required=True, type=int)
    p.add_argument("--max-candidates", required=True, type=int)
    p.add_argument("--cancel-path", required=False, type=Path)
    p.add_argument("--provider", required=True)

    p = sub.add_parser("crack-mask")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--mask", required=True)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--timeout", required=True, type=int)
    p.add_argument("--max-candidates", required=True, type=int)
    p.add_argument("--cancel-path", required=False, type=Path)
    p.add_argument("--provider", required=True)

    p = sub.add_parser("crack-scoped-id-patterns")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--timeout", required=True, type=int)
    p.add_argument("--max-candidates", required=True, type=int)
    p.add_argument("--prefixes", required=True)
    p.add_argument("--cancel-path", required=False, type=Path)
    p.add_argument("--provider", required=True)

    p = sub.add_parser("recover-secret")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)

    p = sub.add_parser("decrypt")
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--secret", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--max-files", required=True, type=int)
    p.add_argument("--max-bytes", required=True, type=int)

    p = sub.add_parser("scan")
    p.add_argument("--target", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--artifact", required=True, type=Path)
    p.add_argument("--rules", required=True, type=Path)
    p.add_argument("--max-files", required=True, type=int)
    p.add_argument("--max-bytes", required=True, type=int)
    return parser


def dispatch_worker_command(argv: list[str]) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "extract-hash":
        return extract_hash(args.input.resolve(), args.output.resolve())
    if args.command == "crack":
        return crack(
            args.hash.resolve(),
            args.wordlist.resolve(),
            args.pot.resolve(),
            args.workdir.resolve(),
            max(1, min(args.timeout, 150)),
            max(1, args.max_candidates),
            args.cancel_path.resolve() if args.cancel_path else None,
            args.provider,
        )
    if args.command == "crack-mask":
        return crack_mask(
            args.hash.resolve(),
            args.mask,
            args.pot.resolve(),
            args.workdir.resolve(),
            max(1, min(args.timeout, 150)),
            max(1, args.max_candidates),
            args.cancel_path.resolve() if args.cancel_path else None,
            args.provider,
        )
    if args.command == "crack-scoped-id-patterns":
        return crack_scoped_id_patterns(
            args.hash.resolve(),
            args.pot.resolve(),
            args.workdir.resolve(),
            max(1, min(args.timeout, 150)),
            max(1, args.max_candidates),
            [part.strip() for part in args.prefixes.split(",") if part.strip()],
            args.cancel_path.resolve() if args.cancel_path else None,
            args.provider,
        )
    if args.command == "recover-secret":
        return recover_secret(args.hash.resolve(), args.pot.resolve(), args.output.resolve(), args.workdir.resolve())
    if args.command == "decrypt":
        return decrypt_file(args.input.resolve(), args.secret.resolve(), args.output_dir.resolve(), args.max_files, args.max_bytes)
    if args.command == "scan":
        return scan(args.target.resolve(), args.report.resolve(), args.artifact.resolve(), args.rules.resolve(), args.max_files, args.max_bytes)
    raise WorkerError("Unknown worker command")


def main() -> None:
    try:
        emit(dispatch_worker_command(sys.argv[1:]))
    except WorkerError as exc:
        emit({"ok": False, "error": str(exc)}, 2)
    except Exception as exc:
        emit({"ok": False, "error": f"Worker error: {type(exc).__name__}"}, 3)


if __name__ == "__main__":
    main()
