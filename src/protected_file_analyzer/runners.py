from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import Settings, get_settings
from .tool_probe import verify_local_toolchain


class RunnerError(RuntimeError):
    pass


@dataclass
class WorkerResult:
    ok: bool
    payload: dict[str, Any]


class ToolRunner(Protocol):
    async def health(self) -> dict[str, Any]: ...
    async def run_worker(self, args: list[str], timeout: int = 180) -> WorkerResult: ...
    def capabilities(self) -> dict[str, Any]: ...


class LocalContainerRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def write_capabilities_cache(self) -> dict[str, Any]:
        caps = self.capabilities()
        self.settings.capabilities_path.write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
        return caps

    async def health(self) -> dict[str, Any]:
        caps = self.write_capabilities_cache()
        return {"status": "healthy" if caps["extractors"]["john"] else "degraded", "backend": "local", "capabilities": caps}

    async def run_worker(self, args: list[str], timeout: int = 180) -> WorkerResult:
        del timeout
        from . import tool_worker

        try:
            payload = await asyncio.to_thread(tool_worker.dispatch_worker_command, args)
        except tool_worker.WorkerError as exc:
            raise RunnerError(str(exc)) from exc
        return WorkerResult(ok=True, payload=payload)

    def capabilities(self) -> dict[str, Any]:
        verified = verify_local_toolchain()
        return {
            "backend": "local",
            "formats": [
                name for name, supported in {
                    "zip": verified["extractors"]["zip2john"],
                    "7z": verified["extractors"]["7z2john"],
                    "pdf": verified["extractors"]["pdf2john"],
                    "doc": verified["extractors"]["office2john"],
                    "docx": verified["extractors"]["office2john"],
                    "docm": verified["extractors"]["office2john"],
                    "xls": verified["extractors"]["office2john"],
                    "xlsx": verified["extractors"]["office2john"],
                    "xlsm": verified["extractors"]["office2john"],
                    "ppt": verified["extractors"]["office2john"],
                    "pptx": verified["extractors"]["office2john"],
                    "pptm": verified["extractors"]["office2john"],
                }.items() if supported
            ],
            "extractors": verified["extractors"],
            "command_paths": verified["command_paths"],
            "scanners": verified["scanners"],
            "wordlists": {
                "rockyou": self.settings.default_rockyou_path.exists(),
                "mounted": [path.name for path in self.settings.mounted_wordlists()],
                "custom_upload": True,
            },
        }


class KaliMcpRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.kali_mcp_url

    def write_capabilities_cache(self) -> dict[str, Any]:
        caps = self.capabilities()
        self.settings.capabilities_path.write_text(json.dumps(caps, ensure_ascii=False, indent=2), encoding="utf-8")
        return caps

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            data = response.json()
            data["backend"] = "kali_mcp"
            self.write_capabilities_cache()
            return data

    async def run_worker(self, args: list[str], timeout: int = 180) -> WorkerResult:
        command = shlex.join([
            self.settings.kali_mcp_worker_python,
            self.settings.kali_mcp_worker_path,
            *args,
        ])
        request_timeout = httpx.Timeout(connect=10, read=max(timeout + 120, 240), write=30, pool=30)
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.post(f"{self.base_url}/api/command", json={"command": command})
            response.raise_for_status()
            result = response.json()
        stdout = result.get("stdout", "")
        payload = self._last_json(stdout)
        if not result.get("success", False) or not payload.get("ok", False):
            raise RunnerError(payload.get("error") or "Kali MCP worker failed")
        return WorkerResult(ok=True, payload=payload)

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": "kali_mcp",
            "formats": ["zip", "7z", "pdf", "doc", "docx", "docm", "xls", "xlsx", "xlsm", "ppt", "pptx", "pptm"],
            "scanners": {},
            "extractors": {},
            "wordlists": {
                "rockyou": self.settings.default_rockyou_path.exists(),
                "mounted": [path.name for path in self.settings.mounted_wordlists()],
                "custom_upload": True,
            },
        }

    @staticmethod
    def _last_json(text: str) -> dict[str, Any]:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        return {"ok": False, "error": "Worker returned no structured result"}


def build_runner(settings: Settings | None = None) -> ToolRunner:
    settings = settings or get_settings()
    if settings.tool_runner_backend == "kali_mcp":
        return KaliMcpRunner(settings)
    return LocalContainerRunner(settings)
