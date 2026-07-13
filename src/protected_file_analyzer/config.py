from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import __version__


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("PFA_APP_NAME", "Protected File Analyzer")
    version: str = __version__
    data_root: Path = _path("PFA_DATA_ROOT", str(PROJECT_ROOT / "data"))
    static_dir: Path = _path("PFA_STATIC_DIR", str(PACKAGE_ROOT / "static"))
    yara_rules_path: Path = _path("PFA_YARA_RULES_PATH", str(PACKAGE_ROOT / "rules" / "basic_static.yar"))
    wordlists_dir: Path = _path("PFA_WORDLISTS_DIR", str(PROJECT_ROOT / "wordlists"))
    default_rockyou_path: Path = _path("PFA_DEFAULT_ROCKYOU_PATH", str(PROJECT_ROOT / "wordlists" / "rockyou.txt"))
    tool_runner_backend: str = os.getenv("PFA_TOOL_RUNNER_BACKEND", "local").strip().lower()
    kali_mcp_url: str = os.getenv("PFA_KALI_MCP_URL", "http://127.0.0.1:5000").rstrip("/")
    kali_mcp_worker_python: str = os.getenv("PFA_KALI_MCP_WORKER_PYTHON", "/usr/bin/python3")
    kali_mcp_worker_path: str = os.getenv("PFA_KALI_MCP_WORKER_PATH", "/opt/protected-file-analyzer/worker/kali_worker.py")
    max_file_bytes: int = int(os.getenv("PFA_MAX_FILE_MB", "100")) * 1024 * 1024
    max_wordlist_bytes: int = int(os.getenv("PFA_MAX_WORDLIST_MB", "200")) * 1024 * 1024
    max_extracted_bytes: int = int(os.getenv("PFA_MAX_EXTRACTED_MB", "500")) * 1024 * 1024
    max_extracted_files: int = int(os.getenv("PFA_MAX_EXTRACTED_FILES", "5000"))
    crack_timeout_seconds: int = min(int(os.getenv("PFA_CRACK_TIMEOUT_SECONDS", "120")), 150)
    max_concurrent_cracks: int = max(1, int(os.getenv("PFA_MAX_CONCURRENT_CRACKS", "1")))
    job_ttl_minutes: int = int(os.getenv("PFA_JOB_TTL_MINUTES", "60"))
    bind_host: str = os.getenv("PFA_BIND_HOST", "127.0.0.1")
    bind_port: int = int(os.getenv("PFA_BIND_PORT", "8088"))
    worker_poll_interval_seconds: float = float(os.getenv("PFA_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    cleanup_interval_seconds: float = float(os.getenv("PFA_CLEANUP_INTERVAL_SECONDS", "900"))
    reveal_display_seconds: int = int(os.getenv("PFA_REVEAL_DISPLAY_SECONDS", "30"))
    clamav_enabled: bool = os.getenv("PFA_CLAMAV_ENABLED", "1").lower() not in {"0", "false", "no"}
    secret_key: str = os.getenv("PFA_SECRET_KEY", "change-me")

    @property
    def capabilities_path(self) -> Path:
        return self.data_root / "_capabilities.json"

    def mounted_wordlists(self) -> list[Path]:
        if not self.wordlists_dir.exists():
            return []
        return sorted(
            [p for p in self.wordlists_dir.iterdir() if p.is_file() and p.suffix.lower() in {".txt", ".lst", ".dic", ".wordlist"}],
            key=lambda p: p.name.lower(),
        )

    def load_cached_capabilities(self) -> dict:
        if not self.capabilities_path.exists():
            return {}
        try:
            return json.loads(self.capabilities_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.wordlists_dir.mkdir(parents=True, exist_ok=True)
    return settings
