from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .runners import ToolRunner, build_runner
from .security import ensure_within, validate_extension
from .store import JobStore

VALID_WORDLIST_MODES = {"rockyou", "pin4", "israeli_id", "custom", "mounted"}


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
            runner_health = await runner.health() if settings.tool_runner_backend == "kali_mcp" else {"status": "healthy" if cached_caps else "starting", "backend": runner_status, "capabilities": cached_caps}
            ready = runner_health.get("status") in {"healthy", "degraded"}
        except Exception:
            runner_health = {"status": "unreachable", "backend": runner_status, "capabilities": cached_caps}
            ready = False
        return {
            "web": "healthy",
            "runner": runner_health,
            "ready": ready,
            "runner_backend": runner_status,
            "rockyou_present": bool(cached_caps.get("wordlists", {}).get("rockyou", settings.default_rockyou_path.exists())),
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
            "wordlists": cached_caps.get("wordlists", {}),
        }

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        protected_file: UploadFile = File(...),
        wordlist_mode: str = Form("rockyou"),
        custom_wordlist: UploadFile | None = File(None),
        mounted_wordlist_name: str | None = Form(None),
        authorization_confirmed: bool = Form(False),
    ) -> dict:
        if not authorization_confirmed:
            raise HTTPException(status_code=400, detail="Authorization confirmation is required")
        if wordlist_mode not in VALID_WORDLIST_MODES:
            raise HTTPException(status_code=400, detail="Invalid wordlist mode")
        try:
            ext = validate_extension(protected_file.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if wordlist_mode == "custom" and not custom_wordlist:
            raise HTTPException(status_code=400, detail="A custom wordlist file is required")
        if wordlist_mode == "rockyou" and not settings.default_rockyou_path.exists():
            raise HTTPException(status_code=503, detail="rockyou.txt is not available in this deployment")
        if wordlist_mode == "mounted" and not mounted_wordlist_name:
            raise HTTPException(status_code=400, detail="A mounted wordlist name is required")

        job_id = uuid.uuid4().hex
        job_dir = store.create(job_id, {"original_name": protected_file.filename, "wordlist_mode": wordlist_mode})
        source = job_dir / "input" / f"protected{ext}"
        try:
            size = await save_limited(protected_file, source, settings.max_file_bytes)
            update_payload = {"source_relative": str(source.relative_to(job_dir)), "source_size": size}
            if wordlist_mode == "custom":
                wordlist = job_dir / "input" / "custom-wordlist.txt"
                await save_limited(custom_wordlist, wordlist, settings.max_wordlist_bytes)  # type: ignore[arg-type]
                update_payload["wordlist_path"] = str(wordlist)
            elif wordlist_mode == "rockyou":
                update_payload["wordlist_path"] = str(settings.default_rockyou_path)
            elif wordlist_mode == "mounted":
                candidate = ensure_within(settings.wordlists_dir, settings.wordlists_dir / mounted_wordlist_name)
                if not candidate.exists() or not candidate.is_file():
                    raise HTTPException(status_code=400, detail="Mounted wordlist was not found")
                update_payload["wordlist_path"] = str(candidate)
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
        state.pop("wordlist_path", None)
        state.pop("source_relative", None)
        return state

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

    @app.post("/api/jobs/{job_id}/reveal-password")
    async def reveal_password(job_id: str):
        try:
            state = store.get(job_id)
            job_dir = store.job_dir(job_id)
            path = ensure_within(job_dir, job_dir / "work" / "reveal-password.secret")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Job not found")
        if state.get("status") != "completed" or not state.get("password_available"):
            raise HTTPException(status_code=409, detail="Password is not available")
        if not path.is_file() or path.stat().st_size > 4096:
            raise HTTPException(status_code=410, detail="Recovered password is no longer available")
        try:
            password = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=410, detail="Recovered password is no longer available") from exc
        if not password:
            raise HTTPException(status_code=410, detail="Recovered password is no longer available")
        store.update(job_id, reveal_count=int(state.get("reveal_count", 0)) + 1, last_revealed_at=time.time())
        return JSONResponse({"password": password, "display_seconds": settings.reveal_display_seconds}, headers={"Cache-Control": "no-store, no-cache, max-age=0, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str):
        try:
            store.delete(job_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Job not found")
        return None

    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
    return app


app = create_app()
