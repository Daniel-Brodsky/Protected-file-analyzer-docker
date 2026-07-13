from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .security import ensure_within, validate_job_id


class JobStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def job_dir(self, job_id: str) -> Path:
        validate_job_id(job_id)
        return ensure_within(self.settings.data_root, self.settings.data_root / job_id)

    def state_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "status.json"

    def cancel_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / ".cancel"

    def create(self, job_id: str, payload: dict[str, Any]) -> Path:
        job_dir = self.job_dir(job_id)
        self._mkdir_private(job_dir)
        self._mkdir_private(job_dir / "input")
        self._mkdir_private(job_dir / "work")
        self._mkdir_private(job_dir / "output")
        self._mkdir_private(job_dir / "logs")
        now = time.time()
        state = {
            "job_id": job_id,
            "status": "pending",
            "atomic_state": "pending",
            "stage": "preparing",
            "progress": 0,
            "message": "Preparing",
            "created_at": now,
            "updated_at": now,
            "artifact_ready": False,
            "report_ready": False,
            **payload,
        }
        self._write_atomic(self.state_path(job_id), state)
        return job_dir

    def get(self, job_id: str) -> dict[str, Any]:
        path = self.state_path(job_id)
        if not path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        state = self.get(job_id)
        for forbidden in ("password", "secret", "recovered_password"):
            changes.pop(forbidden, None)
        state.update(changes)
        state["updated_at"] = time.time()
        self._write_atomic(self.state_path(job_id), state)
        if state.get("atomic_state") in {"completed", "failed", "cancelled"}:
            self.release_claim(job_id)
        return state

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        state = self.get(job_id)
        if state.get("atomic_state") in {"completed", "failed", "cancelled"}:
            return state
        cancel_path = self.cancel_path(job_id)
        cancel_path.write_text("cancel\n", encoding="utf-8")
        cancel_path.chmod(0o600)
        if state.get("atomic_state") == "pending":
            self._cleanup_runtime_payload(job_id)
            self.clear_cancel_request(job_id)
            return self.update(job_id, status="cancelled", atomic_state="cancelled", stage="completed", progress=100, message="Cancelled")
        return self.update(job_id, status="cancelling", atomic_state="cancelling", message="Cancelling")

    def is_cancel_requested(self, job_id: str) -> bool:
        try:
            state = self.get(job_id)
        except FileNotFoundError:
            return False
        return self.cancel_path(job_id).exists() or state.get("atomic_state") == "cancelling"

    def clear_cancel_request(self, job_id: str) -> None:
        self.cancel_path(job_id).unlink(missing_ok=True)

    def delete(self, job_id: str) -> None:
        job_dir = self.job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def cleanup_expired(self) -> int:
        cutoff = time.time() - self.settings.job_ttl_minutes * 60
        removed = 0
        for item in self.settings.data_root.iterdir():
            if not item.is_dir() or not validate_dirname(item.name):
                continue
            state_file = item / "status.json"
            try:
                updated = json.loads(state_file.read_text(encoding="utf-8")).get("updated_at", 0)
            except Exception:
                updated = item.stat().st_mtime
            if updated < cutoff:
                shutil.rmtree(item, ignore_errors=True)
                removed += 1
        return removed

    def claim_next_pending(self) -> dict[str, Any] | None:
        candidates = sorted(
            [p for p in self.settings.data_root.iterdir() if p.is_dir() and validate_dirname(p.name)],
            key=lambda p: p.stat().st_mtime,
        )
        for job_dir in candidates:
            job_id = job_dir.name
            claim = job_dir / ".claim"
            try:
                fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                continue
            try:
                state = self.get(job_id)
                if state.get("atomic_state") != "pending":
                    continue
                if self.cancel_path(job_id).exists():
                    self.clear_cancel_request(job_id)
                    self._cleanup_runtime_payload(job_id)
                    self.update(job_id, status="cancelled", atomic_state="cancelled", stage="completed", progress=100, message="Cancelled")
                    continue
                return self.update(job_id, status="running", atomic_state="running", stage="preparing", message="Preparing")
            finally:
                current = self.get(job_id) if self.state_path(job_id).exists() else None
                if not current or current.get("atomic_state") not in {"running", "cancelling"}:
                    claim.unlink(missing_ok=True)
        return None

    def release_claim(self, job_id: str) -> None:
        (self.job_dir(job_id) / ".claim").unlink(missing_ok=True)

    def _cleanup_runtime_payload(self, job_id: str) -> None:
        job_dir = self.job_dir(job_id)
        for name in ("input", "work", "output", "logs"):
            shutil.rmtree(job_dir / name, ignore_errors=True)
        for name in ("artifact.bin", "report.json"):
            (job_dir / name).unlink(missing_ok=True)

    @staticmethod
    def _mkdir_private(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=False)
        path.chmod(0o700)

    @staticmethod
    def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
        path.chmod(0o600)


def validate_dirname(name: str) -> bool:
    try:
        validate_job_id(name)
        return True
    except ValueError:
        return False
