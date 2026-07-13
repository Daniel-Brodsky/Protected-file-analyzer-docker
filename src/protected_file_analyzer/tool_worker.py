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


def crack(hash_file: Path, wordlist: Path, pot: Path, workdir: Path, timeout: int) -> dict[str, Any]:
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
    result: subprocess.CompletedProcess[str] | None = None
    try:
        with temporary_john_environment(john_scope_from_path(workdir)) as env:
            result = run(command, timeout=timeout + 15, cwd=workdir, quiet=False, env=env)
    except subprocess.TimeoutExpired:
        pass
    found = pot.exists() and pot.stat().st_size > 0
    if result is not None:
        raise_for_john_failure(workdir, result, found=found)
    return {"ok": True, "found": found, "timed_out": (not found) and john_timed_out(workdir)}



def crack_mask(hash_file: Path, mask: str, pot: Path, workdir: Path, timeout: int) -> dict[str, Any]:
    john = find_tool("john")
    workdir.mkdir(parents=True, exist_ok=True)
    pot.parent.mkdir(parents=True, exist_ok=True)
    clear_john_session(workdir)
    command = [
        str(john),
        f"--mask={mask}",
        f"--pot={pot}",
        f"--session={workdir / 'john-session'}",
        f"--max-run-time={timeout}",
        "--verbosity=1",
        str(hash_file),
    ]
    result: subprocess.CompletedProcess[str] | None = None
    try:
        with temporary_john_environment(john_scope_from_path(workdir)) as env:
            result = run(command, timeout=timeout + 15, cwd=workdir, quiet=False, env=env)
    except subprocess.TimeoutExpired:
        pass
    found = pot.exists() and pot.stat().st_size > 0
    if result is not None:
        raise_for_john_failure(workdir, result, found=found)
    return {"ok": True, "found": found, "timed_out": (not found) and john_timed_out(workdir)}

def israeli_id_check_digit(first_eight: str) -> str:
    total = 0
    for index, char in enumerate(first_eight):
        value = int(char) * (1 if index % 2 == 0 else 2)
        total += value if value < 10 else value - 9
    return str((10 - (total % 10)) % 10)


def is_valid_israeli_id(value: str) -> bool:
    return bool(re.fullmatch(r"\d{9}", value)) and israeli_id_check_digit(value[:8]) == value[8]


def generate_israeli_id_candidates(chunk_size: int = 5000) -> Iterable[str]:
    buffer: list[str] = []
    for prefix in range(100_000_000):
        first_eight = f"{prefix:08d}"
        buffer.append(first_eight + israeli_id_check_digit(first_eight))
        if len(buffer) >= chunk_size:
            yield "\n".join(buffer) + "\n"
            buffer.clear()
    if buffer:
        yield "\n".join(buffer) + "\n"


def crack_israeli_id(hash_file: Path, pot: Path, workdir: Path, timeout: int) -> dict[str, Any]:
    john = find_tool("john")
    workdir.mkdir(parents=True, exist_ok=True)
    pot.parent.mkdir(parents=True, exist_ok=True)
    batch_path = workdir / "israeli-id-batch.txt"
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        with temporary_john_environment(john_scope_from_path(workdir)) as env:
            for chunk in generate_israeli_id_candidates(chunk_size=50_000):
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    timed_out = True
                    break
                batch_path.write_text(chunk, encoding="utf-8")
                result: subprocess.CompletedProcess[str] | None = None
                try:
                    result = run([
                        str(john),
                        f"--wordlist={batch_path}",
                        f"--pot={pot}",
                        f"--max-run-time={remaining}",
                        "--verbosity=1",
                        str(hash_file),
                    ], timeout=remaining + 5, cwd=workdir, quiet=False, env=env)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    break
                if result is not None:
                    found = pot.exists() and pot.stat().st_size > 0
                    raise_for_john_failure(workdir, result, found=found)
                if pot.exists() and pot.stat().st_size > 0:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
    finally:
        batch_path.unlink(missing_ok=True)
    found = pot.exists() and pot.stat().st_size > 0
    return {"ok": True, "found": found, "timed_out": (not found) and timed_out}


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


def capped(text: str, limit: int = 12000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def optional_tool(command: list[str], timeout: int = 90) -> dict[str, Any]:
    if not shutil.which(command[0]):
        return {"available": False, "output": ""}
    try:
        result = run(command, timeout=timeout)
        return {
            "available": True,
            "return_code": result.returncode,
            "output": capped((result.stdout or "") + (result.stderr or "")),
        }
    except subprocess.TimeoutExpired:
        return {"available": True, "timed_out": True, "output": ""}


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

    scan_subject = str(target)
    clam = optional_tool(["clamscan", "-r", "--infected", "--no-summary", scan_subject], timeout=120)
    yara = ({"available": False, "output": ""} if not rules.exists()
            else optional_tool(["yara", "-r", str(rules), scan_subject], timeout=120))

    office_findings: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() in OFFICE_EXTENSIONS:
            office_findings.append({
                "file": path.name,
                "oleid": optional_tool(["oleid", str(path)], timeout=45),
                "olevba": optional_tool(["olevba", "--analysis", str(path)], timeout=60),
            })

    exiftool = optional_tool(["exiftool", "-json", "-n", "-r", scan_subject], timeout=90)

    pdf_findings: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() == ".pdf":
            pdfid = shutil.which("pdfid.py") or shutil.which("pdfid")
            pdf_findings.append({
                "file": path.name,
                "pdfid": optional_tool([pdfid, str(path)], timeout=45) if pdfid else {"available": False, "output": ""},
            })

    clam_lines = [line.strip() for line in clam.get("output", "").splitlines() if line.strip()]
    clam_hits = any(line.endswith("FOUND") for line in clam_lines)
    yara_lines = [
        line.strip() for line in yara.get("output", "").splitlines()
        if line.strip() and not line.lower().startswith(("error", "warning"))
    ]
    yara_hits = bool(yara_lines)
    macro_hits = any("AutoExec" in f["olevba"].get("output", "") or "Suspicious" in f["olevba"].get("output", "")
                     for f in office_findings)
    score = min(100, (70 if clam_hits else 0) + (25 if yara_hits else 0) + (15 if macro_hits else 0))
    verdict = "malicious_or_high_risk" if clam_hits else "suspicious" if score else "no_obvious_findings"

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
            "score": score,
            "file_count": len(files),
            "total_bytes": total,
            "clamav_hits": clam_hits,
            "yara_hits": yara_hits,
            "macro_indicators": macro_hits,
            "note": "Static findings are indicators, not a final malware verdict.",
        },
        "files": metadata,
        "tools": {
            "clamav": clam,
            "yara": yara,
            "office": office_findings,
            "pdf": pdf_findings,
            "exiftool": exiftool,
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

    p = sub.add_parser("crack-mask")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--mask", required=True)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--timeout", required=True, type=int)

    p = sub.add_parser("crack-israeli-id")
    p.add_argument("--hash", required=True, type=Path)
    p.add_argument("--pot", required=True, type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--timeout", required=True, type=int)

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
        return crack(args.hash.resolve(), args.wordlist.resolve(), args.pot.resolve(), args.workdir.resolve(), max(1, min(args.timeout, 150)))
    if args.command == "crack-mask":
        return crack_mask(args.hash.resolve(), args.mask, args.pot.resolve(), args.workdir.resolve(), max(1, min(args.timeout, 150)))
    if args.command == "crack-israeli-id":
        return crack_israeli_id(args.hash.resolve(), args.pot.resolve(), args.workdir.resolve(), max(1, min(args.timeout, 150)))
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
