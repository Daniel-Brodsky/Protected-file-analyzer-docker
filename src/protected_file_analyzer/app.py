from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .recovery import describe_wordlist_providers
from .runners import ToolRunner, build_runner
from .security import ensure_within, validate_extension
from .store import JobStore


async def save_limited(upload: UploadFile, destination: Path, limit: int) -> int:
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(destination, "wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                await handle.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured limit")
            await handle.write(chunk)
    return written


def create_app(*, settings: Settings | None = None, store: JobStore | None = None, runner: ToolRunner | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or JobStore(settings)
    runner = runner or build_runner(settings)

    async def _cleanup_loop() -> None:
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await asyncio.to_thread(store.cleanup_expired)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(_cleanup_loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict:
        cached_caps = settings.load_cached_capabilities()
        runner_status = cached_caps.get("backend", settings.tool_runner_backend)
        try:
            runner_health = await runner.health() if settings.tool_runner_backend == "kali_mcp" else {
                "status": "healthy" if cached_caps else "starting",
                "backend": runner_status,
                "capabilities": cached_caps,
            }
            ready = runner_health.get("status") in {"healthy", "degraded"}
        except Exception:
            runner_health = {"status": "unreachable", "backend": runner_status, "capabilities": cached_caps}
            ready = False
        providers = cached_caps.get("wordlists") or describe_wordlist_providers(settings)
        return {
            "web": "healthy",
            "runner": runner_health,
            "ready": ready,
            "runner_backend": runner_status,
            "rockyou_present": bool(providers.get("rockyou")),
        }

    @app.get("/api/capabilities")
    async def capabilities() -> dict:
        cached_caps = settings.load_cached_capabilities()
        if not cached_caps:
            cached_caps = runner.capabilities()
        return {
            "runner_backend": cached_caps.get("backend", settings.tool_runner_backend),
            "formats": cached_caps.get("formats", []),
            "extractors": cached_caps.get("extractors", {}),
            "scanners": cached_caps.get("scanners", {}),
            "wordlists": cached_caps.get("wordlists", describe_wordlist_providers(settings)),
        }

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        protected_file: UploadFile = File(...),
        custom_wordlist: UploadFile | None = File(None),
        authorization_confirmed: bool = Form(False),
    ) -> dict:
        if not authorization_confirmed:
            raise HTTPException(status_code=400, detail="Authorization confirmation is required")
        try:
            ext = validate_extension(protected_file.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job_id = uuid.uuid4().hex
        file_format = ext.lstrip(".")
        job_dir = store.create(job_id, {
            "original_name": protected_file.filename,
            "format": file_format,
            "custom_wordlist_supplied": bool(custom_wordlist),
        })
        source = job_dir / "input" / f"protected{ext}"
        try:
            size = await save_limited(protected_file, source, settings.max_file_bytes)
            update_payload = {"source_relative": str(source.relative_to(job_dir)), "source_size": size}
            if custom_wordlist:
                wordlist = job_dir / "input" / "custom-wordlist.txt"
                await save_limited(custom_wordlist, wordlist, settings.max_wordlist_bytes)
                update_payload["custom_wordlist_path"] = str(wordlist)
            store.update(job_id, **update_payload)
        except Exception:
            store.delete(job_id)
            raise
        return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        try:
            state = store.get(job_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        for key in ("source_relative", "custom_wordlist_path"):
            state.pop(key, None)
        return state

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str) -> dict:
        try:
            state = store.request_cancel(job_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "job_id": job_id,
            "status": state.get("status"),
            "message": state.get("message", "Cancelling"),
        }

    @app.get("/api/jobs/{job_id}/report")
    async def report(job_id: str):
        try:
            state = store.get(job_id)
            path = ensure_within(store.job_dir(job_id), store.job_dir(job_id) / "report.json")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        if not state.get("report_ready") or not path.exists():
            raise HTTPException(status_code=409, detail="Report is not ready")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @app.get("/api/jobs/{job_id}/report/download")
    async def download_report(job_id: str):
        path = store.job_dir(job_id) / "report.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Report not found")
        return FileResponse(path, media_type="application/json", filename=f"{job_id}-static-report.json")

    @app.get("/api/jobs/{job_id}/tool-output/{relative_path:path}")
    async def download_tool_output(job_id: str, relative_path: str):
        try:
            job_dir = store.job_dir(job_id)
            path = ensure_within(job_dir, job_dir / relative_path)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Tool output not found")
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)

    @app.get("/api/jobs/{job_id}/artifact")
    async def download_artifact(job_id: str):
        try:
            state = store.get(job_id)
            path = ensure_within(store.job_dir(job_id), store.job_dir(job_id) / "artifact.bin")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        if not state.get("artifact_ready") or not path.exists():
            raise HTTPException(status_code=409, detail="Artifact is not ready")
        return FileResponse(path, media_type="application/octet-stream", filename=state.get("artifact_name", "analysis-artifact.bin"))

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str):
        try:
            state = store.get(job_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        if state.get("atomic_state") not in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Cancel the analysis before deleting it")
        store.delete(job_id)
        return None

    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
    return app


app = create_app()
