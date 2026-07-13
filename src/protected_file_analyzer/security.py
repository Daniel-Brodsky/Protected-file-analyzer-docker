from __future__ import annotations

import re
from pathlib import Path

ALLOWED_EXTENSIONS = {
    ".zip", ".7z", ".pdf",
    ".doc", ".docx", ".docm",
    ".xls", ".xlsx", ".xlsm",
    ".ppt", ".pptx", ".pptm",
}

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def normalized_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_extension(filename: str) -> str:
    ext = normalized_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        supported = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Supported: {supported}")
    return ext


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("Invalid job id")
    return job_id


def ensure_within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escaped the job directory")
    return candidate
