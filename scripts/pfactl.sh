#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PFA_RUNTIME_GID="${PFA_RUNTIME_GID:-$(id -g)}"
cmd="${1:-status}"
case "$cmd" in
  start)
    ./scripts/ensure-runtime-dirs.sh
    docker compose pull
    docker compose up -d
    ;;
  stop) docker compose stop ;;
  restart)
    ./scripts/ensure-runtime-dirs.sh
    docker compose restart
    ;;
  status) docker compose ps ;;
  logs) docker compose logs -f ;;
  update)
    ./scripts/ensure-runtime-dirs.sh
    docker compose pull && docker compose up -d
    ;;
  uninstall) docker compose down -v --remove-orphans ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs|update|uninstall}" >&2; exit 1 ;;
esac
