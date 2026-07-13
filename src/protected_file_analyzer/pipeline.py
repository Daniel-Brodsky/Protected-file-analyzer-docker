from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .config import Settings, get_settings
from .recovery import build_recovery_plan
from .runners import RunnerError, ToolRunner
from .store import JobStore


class PipelineCancelled(RuntimeError):
    pass


async def run_pipeline(job_id: str, *, settings: Settings | None = None, store: JobStore | None = None, runner: ToolRunner) -> None:
    settings = settings or get_settings()
    store = store or JobStore(settings)

    state = store.get(job_id)
    job_dir = store.job_dir(job_id)
    cancel_path = store.cancel_path(job_id)
    source = job_dir / state["source_relative"]
    work = job_dir / "work"
    output = job_dir / "output"
    hash_file = work / "hash.txt"
    pot_file = work / "john.pot"
    secret_file = work / "password.secret"
    report_file = job_dir / "report.json"
    artifact_file = job_dir / "artifact.bin"

    common_limits = [
        "--max-bytes", str(settings.max_extracted_bytes),
        "--max-files", str(settings.max_extracted_files),
    ]
    crack_gate = asyncio.Semaphore(settings.max_concurrent_cracks)

    def ensure_not_cancelled() -> None:
        if store.is_cancel_requested(job_id):
            raise PipelineCancelled("Cancelled")

    def mark_cancelled() -> None:
        store.update(job_id, status="cancelled", atomic_state="cancelled", stage="completed", progress=100, message="Cancelled")

    try:
        ensure_not_cancelled()
        store.update(job_id, status="running", atomic_state="running", stage="preparing", progress=10, message="Preparing")
        extracted = await runner.run_worker(["extract-hash", "--input", str(source), "--output", str(hash_file)])
        ensure_not_cancelled()
        if extracted.payload.get("not_encrypted", False):
            store.update(job_id, stage="static_analysis", progress=85, message="Static analysis")
            scanned = await runner.run_worker([
                "scan",
                "--target", str(source),
                "--report", str(report_file),
                "--artifact", str(artifact_file),
                "--rules", str(settings.yara_rules_path),
                *common_limits,
            ], timeout=150)
            ensure_not_cancelled()
            store.update(
                job_id,
                status="completed",
                atomic_state="completed",
                stage="completed",
                progress=100,
                message="Completed",
                artifact_ready=True,
                report_ready=True,
                artifact_name=scanned.payload.get("artifact_name", f"decrypted-analysis-copy{source.suffix.lower()}"),
                summary=scanned.payload.get("summary", {}),
            )
            return

        plan = build_recovery_plan(settings)
        cracked = None
        timed_out = False
        if plan:
            store.update(job_id, stage="recovering_access", progress=25, message="Recovering access")
        for index, attempt in enumerate(plan, start=1):
            ensure_not_cancelled()
            attempt_progress = 25 + int((35 * index) / max(1, len(plan)))
            store.update(job_id, stage="recovering_access", progress=attempt_progress, message="Recovering access")
            if attempt.kind == "wordlist":
                assert attempt.path is not None
                crack_args = [
                    "crack",
                    "--hash", str(hash_file),
                    "--wordlist", str(attempt.path),
                    "--pot", str(pot_file),
                    "--workdir", str(work),
                    "--timeout", str(attempt.timeout_seconds),
                    "--max-candidates", str(attempt.max_candidates or 0),
                    "--cancel-path", str(cancel_path),
                    "--provider", attempt.source,
                ]
            elif attempt.kind == "mask":
                crack_args = [
                    "crack-mask",
                    "--hash", str(hash_file),
                    "--mask", str(attempt.mask),
                    "--pot", str(pot_file),
                    "--workdir", str(work),
                    "--timeout", str(attempt.timeout_seconds),
                    "--max-candidates", str(attempt.max_candidates or 0),
                    "--cancel-path", str(cancel_path),
                    "--provider", attempt.source,
                ]
            elif attempt.kind == "generator":
                crack_args = [
                    "crack-scoped-id-patterns",
                    "--hash", str(hash_file),
                    "--pot", str(pot_file),
                    "--workdir", str(work),
                    "--timeout", str(attempt.timeout_seconds),
                    "--max-candidates", str(attempt.max_candidates or 0),
                    "--prefixes", ",".join(attempt.prefixes),
                    "--cancel-path", str(cancel_path),
                    "--provider", attempt.source,
                ]
            else:
                raise ValueError(f"Unsupported recovery attempt type: {attempt.kind}")
            async with crack_gate:
                cracked = await runner.run_worker(crack_args, timeout=attempt.timeout_seconds + 25)
            if cracked.payload.get("cancelled", False):
                mark_cancelled()
                return
            if cracked.payload.get("found", False):
                break
            timed_out = timed_out or bool(cracked.payload.get("timed_out", False))

        ensure_not_cancelled()
        if not cracked or not cracked.payload.get("found", False):
            message = "Unable to recover access within configured limits" if timed_out else "Unable to recover access with configured policy"
            store.update(job_id, status="failed", atomic_state="failed", stage="completed", progress=100, message=message)
            return

        store.update(job_id, stage="decrypting", progress=70, message="Decrypting")
        await runner.run_worker([
            "recover-secret",
            "--hash", str(hash_file),
            "--pot", str(pot_file),
            "--output", str(secret_file),
            "--workdir", str(work),
        ])
        ensure_not_cancelled()
        decrypted = await runner.run_worker([
            "decrypt",
            "--input", str(source),
            "--secret", str(secret_file),
            "--output-dir", str(output),
            *common_limits,
        ])
        scan_target = Path(decrypted.payload["scan_target"])

        ensure_not_cancelled()
        store.update(job_id, stage="static_analysis", progress=85, message="Static analysis")
        scanned = await runner.run_worker([
            "scan",
            "--target", str(scan_target),
            "--report", str(report_file),
            "--artifact", str(artifact_file),
            "--rules", str(settings.yara_rules_path),
            *common_limits,
        ], timeout=150)
        ensure_not_cancelled()
        store.update(
            job_id,
            status="completed",
            atomic_state="completed",
            stage="completed",
            progress=100,
            message="Completed",
            artifact_ready=True,
            report_ready=True,
            artifact_name=scanned.payload.get("artifact_name", "analysis-artifact.bin"),
            summary=scanned.payload.get("summary", {}),
        )
    except PipelineCancelled:
        mark_cancelled()
    except (RunnerError, OSError, ValueError) as exc:
        store.update(job_id, status="failed", atomic_state="failed", stage="completed", progress=100, message=str(exc))
    except asyncio.CancelledError:
        mark_cancelled()
        raise
    finally:
        cleanup_paths = [secret_file, pot_file, hash_file, source]
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for item in work.glob("john-session*"):
            item.unlink(missing_ok=True)
        shutil.rmtree(output, ignore_errors=True)
        final_state = store.get(job_id) if store.state_path(job_id).exists() else {}
        if final_state.get("status") != "completed":
            report_file.unlink(missing_ok=True)
            artifact_file.unlink(missing_ok=True)
        store.clear_cancel_request(job_id)
        store.release_claim(job_id)
