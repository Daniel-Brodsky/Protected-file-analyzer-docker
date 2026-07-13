# Contributing

## Development setup

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

For Docker-based testing:

```bash
cp .env.example .env
docker compose build
docker compose up -d
curl http://127.0.0.1:8088/api/health
```

## Test commands

- Local suite: `pytest -q`
- Lint: `ruff check .`
- Live E2E: `PFA_LIVE_BASE_URL=http://127.0.0.1:8088 python -m pytest -q tests/test_live_e2e.py`

## Coding and security expectations

- Do not change application behavior, cracking logic, or container security controls without tests and a clear reason.
- Keep password material out of status JSON, reports, logs, environment variables, image metadata, and command-line arguments.
- Preserve the explicit POST-only reveal flow and its `no-store` response headers.
- Preserve the non-root worker model and per-job John isolation.
- Keep repository paths portable; avoid machine-specific absolute paths.

## Secrets and private data

- Never commit `.env`, production secrets, private keys, API tokens, or real customer files.
- Never commit runtime job data, uploaded samples, decrypted artifacts, or local scan output.
- When adding docs or examples, use placeholders instead of real passwords, user names, hostnames, internal IPs, or local home paths.

## Adding encrypted fixtures safely

- Use synthetic or otherwise non-sensitive sample content only.
- Use fixed **non-sensitive** test passwords only for isolated fixtures and tests.
- Keep fixture passwords out of reports and persisted logs.
- Verify that encrypted fixtures exercise the intended extractor, crack, and decrypt path.
- Prefer format-valid fixtures over hand-built minimal files that do not decrypt with the production toolchain.

## Before opening a PR

- Run `pytest -q`
- Run `ruff check .`
- Rebuild the Docker images if your change affects packaging or runtime behavior.
- Confirm the repository does not contain secrets, `.env`, runtime state, or generated artifacts.