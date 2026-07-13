from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .config import Settings, get_settings
from .runners import RunnerError, ToolRunner
from .store import JobStore


async def run_pipeline(job_id: str, *, settings: Settings | None = None, store: JobStore | None = None, runner: ToolRunner) -> None:
    settings = settings or get_settings()
    store = store or JobStore(settings)

    state = store.get(job_id)
    job_dir = store.job_dir(job_id)
    source = job_dir / state["source_relative"]
    wordlist_mode = state["wordlist_mode"]
    wordlist = Path(state["wordlist_path"]) if state.get("wordlist_path") else None
    work = job_dir / "work"
    output = job_dir / "output"
    hash_file = work / "hash.txt"
    pot_file = work / "john.pot"
    secret_file = work / "password.secret"
    reveal_secret_file = work / "reveal-password.secret"
    report_file = job_dir / "report.json"
    artifact_file = job_dir / "artifact.bin"

    common_limits = [
        "--max-bytes", str(settings.max_extracted_bytes),
        "--max-files", str(settings.max_extracted_files),
    ]
    retain_reveal_secret = False
    crack_gate = asyncio.Semaphore(settings.max_concurrent_cracks)

    try:
        store.update(job_id, status="running", atomic_state="running", stage="extract_hash", progress=10, message="Extracting a JtR-compatible hash")
        extracted = await runner.run_worker(["extract-hash", "--input", str(source), "--output", str(hash_file)])
        if extracted.payload.get("not_encrypted", False):
            store.update(job_id, stage="static_scan", progress=80, message="The file is already unprotected; running static inspection directly")
            scanned = await runner.run_worker([
                "scan", "--target", str(source), "--report", str(report_file), "--artifact", str(artifact_file), "--rules", str(settings.yara_rules_path), *common_limits,
            ], timeout=150)
            store.update(job_id, status="completed", atomic_state="completed", stage="finished", progress=100, message="The file was already unprotected; static analysis completed", artifact_ready=True, report_ready=True, artifact_name=scanned.payload.get("artifact_name", f"decrypted-analysis-copy{source.suffix.lower()}"), summary=scanned.payload.get("summary", {}), password_available=False, reveal_count=0)
            return

        if wordlist_mode == "rockyou":
            if wordlist is None:
                raise ValueError("Wordlist path is missing for the selected mode")
            store.update(job_id, stage="crack", progress=18, message="rockyou selected: trying a fast 4-digit PIN pre-check first")
            async with crack_gate:
                cracked = await runner.run_worker([
                    "crack-mask", "--hash", str(hash_file), "--mask", "?d?d?d?d", "--pot", str(pot_file), "--workdir", str(work), "--timeout", str(settings.crack_timeout_seconds),
                ], timeout=settings.crack_timeout_seconds + 25)
            if not cracked.payload.get("found", False):
                store.update(job_id, stage="crack", progress=30, message="4-digit PIN pre-check did not match; continuing with rockyou")
                async with crack_gate:
                    cracked = await runner.run_worker([
                        "crack", "--hash", str(hash_file), "--wordlist", str(wordlist), "--pot", str(pot_file), "--workdir", str(work), "--timeout", str(settings.crack_timeout_seconds),
                    ], timeout=settings.crack_timeout_seconds + 25)
        else:
            store.update(job_id, stage="crack", progress=30, message="Trying the selected wordlist with John the Ripper")
            if wordlist_mode in {"custom", "mounted"}:
                if wordlist is None:
                    raise ValueError("Wordlist path is missing for the selected mode")
                crack_args = ["crack", "--hash", str(hash_file), "--wordlist", str(wordlist), "--pot", str(pot_file), "--workdir", str(work), "--timeout", str(settings.crack_timeout_seconds)]
            elif wordlist_mode == "pin4":
                crack_args = ["crack-mask", "--hash", str(hash_file), "--mask", "?d?d?d?d", "--pot", str(pot_file), "--workdir", str(work), "--timeout", str(settings.crack_timeout_seconds)]
            elif wordlist_mode == "israeli_id":
                crack_args = ["crack-israeli-id", "--hash", str(hash_file), "--pot", str(pot_file), "--workdir", str(work), "--timeout", str(settings.crack_timeout_seconds)]
            else:
                raise ValueError("Unsupported wordlist mode")
            async with crack_gate:
                cracked = await runner.run_worker(crack_args, timeout=settings.crack_timeout_seconds + 25)

        if not cracked.payload.get("found", False):
            if cracked.payload.get("timed_out", False):
                store.update(job_id, status="timed_out", atomic_state="failed", stage="finished", progress=100, message="The selected wordlist hit the time limit before all candidates were tried")
            else:
                store.update(job_id, status="not_cracked", atomic_state="failed", stage="finished", progress=100, message="No matching password was found in the selected wordlist")
            return

        store.update(job_id, stage="recover_secret", progress=50, message="Preparing a hidden one-time decryption secret")
        await runner.run_worker(["recover-secret", "--hash", str(hash_file), "--pot", str(pot_file), "--output", str(secret_file), "--workdir", str(work)])
        shutil.copyfile(secret_file, reveal_secret_file)
        reveal_secret_file.chmod(0o600)

        store.update(job_id, stage="decrypt", progress=65, message="Creating an unprotected analysis copy")
        decrypted = await runner.run_worker([
            "decrypt", "--input", str(source), "--secret", str(secret_file), "--output-dir", str(output), *common_limits,
        ])
        scan_target = Path(decrypted.payload["scan_target"])

        store.update(job_id, stage="static_scan", progress=80, message="Running static inspection without executing the file")
        scanned = await runner.run_worker([
            "scan", "--target", str(scan_target), "--report", str(report_file), "--artifact", str(artifact_file), "--rules", str(settings.yara_rules_path), *common_limits,
        ], timeout=150)
        store.update(job_id, status="completed", atomic_state="completed", stage="finished", progress=100, message="Decryption and static analysis completed", artifact_ready=True, report_ready=True, artifact_name=scanned.payload.get("artifact_name", "analysis-artifact.bin"), summary=scanned.payload.get("summary", {}), password_available=True, reveal_count=0)
        retain_reveal_secret = True
    except (RunnerError, OSError, ValueError) as exc:
        store.update(job_id, status="failed", atomic_state="failed", stage="failed", progress=100, message=str(exc))
    except asyncio.CancelledError:
        store.update(job_id, status="cancelled", atomic_state="failed", stage="cancelled", progress=100, message="Job was cancelled")
        raise
    finally:
        for path in (secret_file, pot_file, hash_file, source):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if not retain_reveal_secret:
            reveal_secret_file.unlink(missing_ok=True)
        shutil.rmtree(output, ignore_errors=True)
        if wordlist_mode == "custom" and wordlist is not None:
            wordlist.unlink(missing_ok=True)
        store.release_claim(job_id)
