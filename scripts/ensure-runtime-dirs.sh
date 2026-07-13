#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$ROOT/runtime"
JOBS_DIR="$RUNTIME_ROOT/jobs"
WORDLISTS_DIR="$RUNTIME_ROOT/wordlists"
HOST_UID="$(id -u)"
HOST_GID="${PFA_RUNTIME_GID:-$(id -g)}"

mkdir -p "$JOBS_DIR" "$WORDLISTS_DIR"
chmod 0755 "$RUNTIME_ROOT"
chmod 2775 "$JOBS_DIR" "$WORDLISTS_DIR"

if command -v chgrp >/dev/null 2>&1; then
  chgrp "$HOST_GID" "$RUNTIME_ROOT" "$JOBS_DIR" "$WORDLISTS_DIR" 2>/dev/null || true
fi

if command -v docker >/dev/null 2>&1; then
  docker run --rm \
    -e HOST_UID="$HOST_UID" \
    -e HOST_GID="$HOST_GID" \
    -v "$RUNTIME_ROOT:/runtime" \
    alpine:3.20 \
    sh -eu -c '
      install -d -m 0755 -o "$HOST_UID" -g "$HOST_GID" /runtime
      install -d -m 2775 -o "$HOST_UID" -g "$HOST_GID" /runtime/jobs /runtime/wordlists
      chown "$HOST_UID:$HOST_GID" /runtime /runtime/jobs /runtime/wordlists
      chmod 0755 /runtime
      chmod 2775 /runtime/jobs /runtime/wordlists
    '
fi

printf 'Prepared runtime directories:\n'
stat -c '%A %u:%g %n' "$RUNTIME_ROOT" "$JOBS_DIR" "$WORDLISTS_DIR"
