# Protected File Analyzer

Protected File Analyzer is a Docker-first web application for **authorized** analysis of password-protected files. It accepts a protected file, extracts a crackable hash with John the Ripper helper tools (`*2john`), attempts password recovery with an operator-selected wordlist mode, decrypts the file when recovery succeeds, and runs static analysis on the decrypted output.

## What problem this project solves

Teams that are authorized to inspect protected files often need a repeatable workflow for:

- identifying supported encrypted formats
- extracting a crackable hash with the right helper tool
- attempting controlled password recovery with explicit operator input
- decrypting the recovered file safely
- running static analysis on the decrypted artifact

This project packages that workflow behind a small web UI and API while keeping password-recovery logic in a separate non-root worker.

## Main capabilities

- Browser UI plus JSON API
- Separate web and worker services
- Verified John the Ripper + `*2john` extractor usage
- Custom, mounted, and built-in wordlist modes
- Decryption and artifact download after successful recovery
- Static analysis with YARA, oletools, PDFiD, ExifTool, and optional ClamAV
- Password redaction in status and report outputs
- Explicit POST-only password reveal endpoint with `Cache-Control: no-store`

## Supported protected-file types

- ZIP: `.zip`
- 7-Zip: `.7z`
- PDF: `.pdf`
- Microsoft Office:
  - Word: `.doc`, `.docx`, `.docm`
  - Excel: `.xls`, `.xlsx`, `.xlsm`
  - PowerPoint: `.ppt`, `.pptx`, `.pptm`

## General architecture

- **Web service**: FastAPI UI and API for uploads, job status, reports, artifacts, and explicit password reveal.
- **Non-root worker**: a separate worker process polls shared job state and performs extraction, cracking, decryption, and static analysis.
- **John / `*2john`**: the worker verifies and uses John the Ripper plus format-specific extractors such as `zip2john`, `7z2john`, `pdf2john`, and `office2john`.
- **Static analysis**: after successful decryption, the worker analyzes the output with enabled scanners.
- **Shared runtime directories**: the web and worker containers share job and wordlist directories through bind mounts.

## Repository structure

The current structure is already stable and does not need a risky refactor just to match a theoretical layout:

```text
protected-file-analyzer/
├── .github/workflows/
├── docker/
│   └── Dockerfile
├── scripts/
│   ├── ensure-runtime-dirs.sh
│   └── pfactl.sh
├── src/
│   └── protected_file_analyzer/
├── tests/
│   └── fixtures/
├── wordlists/
├── .env.example
├── .gitignore
├── .dockerignore
├── compose.yaml
├── install.sh
├── install.ps1
├── pyproject.toml
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

Notes:

- `runtime/` and `data/` are created locally at runtime and are intentionally Git-ignored.
- The Compose file is `compose.yaml`.
- The Docker build definition is `docker/Dockerfile`.

## System requirements

### Recommended

- Docker Engine with Docker Compose
- Linux, macOS, or Windows host capable of running Docker
- Enough local disk space for uploaded files, decrypted artifacts, and reports

### For non-Docker local development

- Python `3.11`
- John the Ripper and supported `*2john` helpers available on the host
- scanner dependencies available on the host if you want parity with the container workflow

## Local installation

### Option A: Docker Compose

From the repository root:

```bash
cd protected-file-analyzer
cp .env.example .env
./scripts/pfactl.sh start
```

Open the UI at:

- `http://127.0.0.1:8088`

To stop it later:

```bash
cd protected-file-analyzer
./scripts/pfactl.sh stop
```

### Option B: direct Compose commands

If you prefer raw Compose commands:

```bash
cd protected-file-analyzer
cp .env.example .env
./scripts/ensure-runtime-dirs.sh
docker compose up -d --build
```

> If you bypass the setup/start flow and run `docker compose up` directly from a fresh tree, Docker may create runtime bind-mount directories with ownership or permissions that prevent the worker from writing. Use `./scripts/pfactl.sh start` or `./scripts/ensure-runtime-dirs.sh` first.

## Running without Docker

Running without Docker is supported for local development, but it is less isolated and requires the host toolchain to match the expected worker dependencies.

Example development setup:

```bash
cd protected-file-analyzer
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

# Terminal 1: web app
uvicorn protected_file_analyzer.app:app --host 127.0.0.1 --port 8088

# Terminal 2: worker loop
python -m protected_file_analyzer
```

For non-Docker runs, make sure the required host tools are available. The Docker deployment remains the recommended path.

## Environment variables

All supported configuration lives in `.env.example`.

### Required for normal local use

- `PFA_BIND_HOST`
- `PFA_BIND_PORT`
- `PFA_SECRET_KEY`

### Common runtime settings

- `PFA_APP_NAME`
- `PFA_DATA_ROOT`
- `PFA_STATIC_DIR`
- `PFA_WORDLISTS_DIR`
- `PFA_DEFAULT_ROCKYOU_PATH`
- `PFA_YARA_RULES_PATH`
- `PFA_TOOL_RUNNER_BACKEND`
- `PFA_RUNTIME_GID`

### Optional / advanced settings

- `PFA_KALI_MCP_URL`
- `PFA_KALI_MCP_WORKER_PYTHON`
- `PFA_KALI_MCP_WORKER_PATH`
- `PFA_MAX_FILE_MB`
- `PFA_MAX_WORDLIST_MB`
- `PFA_MAX_EXTRACTED_MB`
- `PFA_MAX_EXTRACTED_FILES`
- `PFA_CRACK_TIMEOUT_SECONDS`
- `PFA_MAX_CONCURRENT_CRACKS`
- `PFA_WORKER_POLL_INTERVAL_SECONDS`
- `PFA_CLEANUP_INTERVAL_SECONDS`
- `PFA_JOB_TTL_MINUTES`
- `PFA_REVEAL_DISPLAY_SECONDS`
- `PFA_CLAMAV_ENABLED`

## Example `.env`

```dotenv
PFA_BIND_HOST=127.0.0.1
PFA_BIND_PORT=8088
PFA_APP_NAME=Protected File Analyzer
PFA_TOOL_RUNNER_BACKEND=local
PFA_RUNTIME_GID=10001
PFA_SECRET_KEY=change-me
```

Notes:

- `.env` is intentionally ignored by Git.
- Keep `PFA_SECRET_KEY` local and replace the placeholder before real use.
- On Linux and macOS, the setup/start flow aligns runtime directory permissions for the container group.

## Health endpoint

```bash
curl --fail http://127.0.0.1:8088/api/health
```

Look for:

- `"ready": true`

## Example API workflow

Create a temporary custom wordlist and submit an authorized job:

```bash
cd protected-file-analyzer
mkdir -p ./tmp
printf '%s\n' '<TEST_PASSWORD>' 'not-it' > ./tmp/wordlist.txt

curl -X POST http://127.0.0.1:8088/api/jobs \
  -F 'authorization_confirmed=true' \
  -F 'wordlist_mode=custom' \
  -F 'protected_file=@./sample.pdf;type=application/pdf' \
  -F 'custom_wordlist=@./tmp/wordlist.txt;type=text/plain'
```

Poll status:

```bash
curl http://127.0.0.1:8088/api/jobs/<JOB_ID>
```

Fetch outputs:

```bash
curl http://127.0.0.1:8088/api/jobs/<JOB_ID>/report
curl -OJ http://127.0.0.1:8088/api/jobs/<JOB_ID>/artifact
curl -X POST http://127.0.0.1:8088/api/jobs/<JOB_ID>/reveal-password
curl -X DELETE http://127.0.0.1:8088/api/jobs/<JOB_ID>
```

## Custom wordlist usage

Supported wordlist modes include:

- `custom`
- `mounted`
- `rockyou`
- `pin4`
- `israeli_id`

Wordlists can be supplied by:

1. uploading a per-job custom wordlist
2. placing mounted wordlists under `./runtime/wordlists/`

If `rockyou.txt` is not present in the deployment, the API returns a clear error instead of silently pretending it is available.

## Running tests

```bash
cd protected-file-analyzer
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## Running lint

```bash
cd protected-file-analyzer
. .venv/bin/activate
ruff check .
```

## Running live end-to-end tests

With the stack already running locally:

```bash
cd protected-file-analyzer
. .venv/bin/activate
PFA_LIVE_BASE_URL=http://127.0.0.1:8088 python -m pytest -q tests/test_live_e2e.py
```

## Known limitations

- No default `rockyou.txt` is bundled with the repository or image.
- The default deployment is intended for local or otherwise controlled environments, not open internet exposure.
- The non-Docker path depends on the host toolchain and is less isolated than the container deployment.
- The optional `kali_mcp` backend requires a separately managed compatible service.

## Security considerations

- The worker runs as **UID 10001**.
- The default local worker has **no exposed ports** and uses `network_mode: none`.
- Compose applies `cap_drop: ALL`, `no-new-privileges:true`, a read-only root filesystem, and `tmpfs` for `/tmp`.
- Every John invocation gets an **isolated per-job `HOME`** with a private `.john` directory.
- Successful crack output is **redacted** before being persisted.
- Job status and report JSON do **not** expose recovered passwords.
- The reveal endpoint is **POST-only** and returns `Cache-Control: no-store`.

See also: `SECURITY.md`

## Legal and authorized-use notice

Use this project only for files, systems, and environments you own or are explicitly authorized to analyze. Do not use it for unauthorized password recovery, unauthorized access attempts, or handling of third-party protected material without permission.

## Troubleshooting

### `ready` stays `false`

- Check `docker compose logs web worker`
- Confirm the worker can write to the runtime bind mounts
- Run `./scripts/ensure-runtime-dirs.sh` and start again

### Worker fails with a permission error under `/data/jobs`

- You likely started Compose directly from a fresh tree without preparing runtime directories
- Stop the stack, run `./scripts/ensure-runtime-dirs.sh`, then start again

### `rockyou` mode returns unavailable

- This is expected unless you mount or provide a valid `rockyou.txt`

### Live E2E test is skipped

- Set `PFA_LIVE_BASE_URL`, then rerun `tests/test_live_e2e.py`

## License

This repository currently includes an MIT `LICENSE` file.