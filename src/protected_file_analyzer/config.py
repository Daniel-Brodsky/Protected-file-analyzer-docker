from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def _path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


def _bounded_int(name: str, default: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    value = int(os.getenv(name, default))
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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
    crack_timeout_seconds: int = _bounded_int("PFA_CRACK_TIMEOUT_SECONDS", "120", maximum=150)
    max_concurrent_cracks: int = max(1, int(os.getenv("PFA_MAX_CONCURRENT_CRACKS", "1")))
    job_ttl_minutes: int = int(os.getenv("PFA_JOB_TTL_MINUTES", "60"))
    bind_host: str = os.getenv("PFA_BIND_HOST", "127.0.0.1")
    bind_port: int = int(os.getenv("PFA_BIND_PORT", "8088"))
    worker_poll_interval_seconds: float = float(os.getenv("PFA_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    cleanup_interval_seconds: float = float(os.getenv("PFA_CLEANUP_INTERVAL_SECONDS", "900"))
    secret_key: str = os.getenv("PFA_SECRET_KEY", "change-me")

    recovery_custom_timeout_seconds: int = _bounded_int("PFA_RECOVERY_CUSTOM_TIMEOUT_SECONDS", "45", maximum=150)
    recovery_custom_max_candidates: int = _bounded_int("PFA_RECOVERY_CUSTOM_MAX_CANDIDATES", "50000")
    recovery_mounted_timeout_seconds: int = _bounded_int("PFA_RECOVERY_MOUNTED_TIMEOUT_SECONDS", "45", maximum=150)
    recovery_mounted_max_candidates: int = _bounded_int("PFA_RECOVERY_MOUNTED_MAX_CANDIDATES", "50000")
    recovery_pin_timeout_seconds: int = _bounded_int("PFA_RECOVERY_PIN_TIMEOUT_SECONDS", "30", maximum=150)
    recovery_pin_max_candidates: int = _bounded_int("PFA_RECOVERY_PIN_MAX_CANDIDATES", "10000", maximum=10000)
    recovery_rockyou_timeout_seconds: int = _bounded_int("PFA_RECOVERY_ROCKYOU_TIMEOUT_SECONDS", "60", maximum=150)
    recovery_rockyou_max_candidates: int = _bounded_int("PFA_RECOVERY_ROCKYOU_MAX_CANDIDATES", "200000")
    scoped_org_patterns_enabled: bool = _truthy_env("PFA_SCOPED_ORG_PATTERNS_ENABLED", "0")
    scoped_org_pattern_timeout_seconds: int = _bounded_int("PFA_SCOPED_ORG_PATTERN_TIMEOUT_SECONDS", "30", maximum=150)
    scoped_org_pattern_max_candidates: int = _bounded_int("PFA_SCOPED_ORG_PATTERN_MAX_CANDIDATES", "20000")
    scoped_org_id_prefixes_raw: str = os.getenv("PFA_SCOPED_ORG_ID_PREFIXES", "").strip()

    tool_output_ui_max_bytes: int = _bounded_int("PFA_TOOL_OUTPUT_UI_MAX_BYTES", "16384")
    tool_output_download_max_bytes: int = _bounded_int("PFA_TOOL_OUTPUT_DOWNLOAD_MAX_BYTES", "262144")

    @property
    def capabilities_path(self) -> Path:
        return self.data_root / "_capabilities.json"

    def mounted_wordlists(self) -> list[Path]:
        if not self.wordlists_dir.exists():
            return []
        mounted = [
            p for p in self.wordlists_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in {".txt", ".lst", ".dic", ".wordlist"}
            and p.resolve() != self.default_rockyou_path
        ]
        return sorted(mounted, key=lambda p: p.name.lower())

    def scoped_org_id_prefixes(self) -> list[str]:
        prefixes: list[str] = []
        for raw in self.scoped_org_id_prefixes_raw.split(","):
            value = raw.strip()
            if not value:
                continue
            if value.isdigit() and 1 <= len(value) <= 8:
                prefixes.append(value)
        return prefixes

    def load_cached_capabilities(self) -> dict:
        if not self.capabilities_path.exists():
            return {}
        try:
            return json.loads(self.capabilities_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def get_settings() -> Settings:
    settings = Settings()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.wordlists_dir.mkdir(parents=True, exist_ok=True)
    return settings
