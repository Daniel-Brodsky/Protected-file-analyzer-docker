#!/usr/bin/env bash
set -euo pipefail

VERSION="${PFA_VERSION:-0.1.0}"
REPO="${PFA_REPO:-OWNER/REPO}"
BASE_URL="${PFA_RELEASE_BASE_URL:-https://github.com/${REPO}/releases/download/v${VERSION}}"
ARCHIVE_NAME="protected-file-analyzer-${VERSION}.tar.gz"
ARCHIVE_URL="${BASE_URL}/${ARCHIVE_NAME}"
CHECKSUM_URL="${BASE_URL}/${ARCHIVE_NAME}.sha256"
INSTALL_DIR="${PFA_INSTALL_DIR:-$HOME/protected-file-analyzer}"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need docker
need curl
need tar
need sha256sum

docker version >/dev/null
docker compose version >/dev/null

curl -fsSL "$ARCHIVE_URL" -o "$TMP_DIR/$ARCHIVE_NAME"
curl -fsSL "$CHECKSUM_URL" -o "$TMP_DIR/$ARCHIVE_NAME.sha256"
(
  cd "$TMP_DIR"
  sha256sum -c "$ARCHIVE_NAME.sha256"
)

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -xzf "$TMP_DIR/$ARCHIVE_NAME" -C "$INSTALL_DIR" --strip-components=1
cd "$INSTALL_DIR"

export PFA_RUNTIME_GID="${PFA_RUNTIME_GID:-$(id -g)}"
./scripts/ensure-runtime-dirs.sh

if [ ! -f .env ]; then
  cp .env.example .env
fi
python3 - <<'PY'
from pathlib import Path
import os
import secrets
path = Path('.env')
text = path.read_text(encoding='utf-8')
if 'PFA_SECRET_KEY=change-me' in text:
    text = text.replace('PFA_SECRET_KEY=change-me', f'PFA_SECRET_KEY={secrets.token_urlsafe(32)}')
runtime_gid = os.environ.get('PFA_RUNTIME_GID', '').strip()
if runtime_gid:
    lines = []
    updated = False
    for line in text.splitlines():
        if line.startswith('PFA_RUNTIME_GID='):
            lines.append(f'PFA_RUNTIME_GID={runtime_gid}')
            updated = True
        else:
            lines.append(line)
    if not updated:
        lines.append(f'PFA_RUNTIME_GID={runtime_gid}')
    text = '\n'.join(lines) + '\n'
path.write_text(text, encoding='utf-8')
PY

./scripts/pfactl.sh start
./scripts/pfactl.sh status

echo "Installed into $INSTALL_DIR"
