#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker() {
  if command docker info >/dev/null 2>&1; then command docker "$@"; else sudo docker "$@"; fi
}

dry_run=0
[[ "${1:-}" == "--dry-run" ]] && dry_run=1
if (($# > 0)) && (( ! dry_run )); then echo "Usage: $0 [--dry-run]" >&2; exit 2; fi
if ((dry_run)); then
  echo "Stop processes that write data, archive ./data and .env in backups/ with permissions 600, then restart those processes."
  exit 0
fi
[[ -d data && -f .env ]] || { echo "data/ or .env is missing. Run the installer first." >&2; exit 1; }
docker info >/dev/null
mkdir -p backups
chmod 700 backups
archive="backups/noname-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
umask 077
mapfile -t active < <(docker compose ps --status running --services 2>/dev/null | grep -E '^(web|worker|worker-consilium|scheduler)$' || true)
resume() {
  if (("${#active[@]}")); then docker compose start "${active[@]}" >/dev/null || true; fi
}
trap resume EXIT
if (("${#active[@]}")); then docker compose stop "${active[@]}"; fi
tar -czf "$archive" data .env
chmod 600 "$archive"
echo "Backup: $archive"
