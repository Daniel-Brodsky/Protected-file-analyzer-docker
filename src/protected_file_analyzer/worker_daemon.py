from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import Settings, get_settings
from .pipeline import run_pipeline
from .runners import build_runner
from .store import JobStore


def _assert_directory_writable(path: Path, label: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / '.pfa-write-test'
    try:
        probe.write_text('ok', encoding='utf-8')
    except OSError as exc:
        raise RuntimeError(
            f'{label} is not writable: {path}. Prepare runtime directories with the setup/start flow before running Compose directly.'
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


def ensure_runtime_writable(settings: Settings) -> None:
    _assert_directory_writable(settings.data_root, 'PFA data root')
    _assert_directory_writable(settings.wordlists_dir, 'PFA wordlists directory')


async def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    ensure_runtime_writable(settings)
    store = JobStore(settings)
    runner = build_runner(settings)
    write_cache = getattr(runner, "write_capabilities_cache", None)
    if callable(write_cache):
        await asyncio.to_thread(write_cache)
    while True:
        claimed = await asyncio.to_thread(store.claim_next_pending)
        if claimed is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
            continue
        job_id = claimed["job_id"]
        await run_pipeline(job_id, settings=settings, store=store, runner=runner)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_forever())
    except RuntimeError as exc:
        logging.error('%s', exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
