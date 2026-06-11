#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
BACKUP_DIR=""
CONFIRM=""

usage() {
  echo "Usage: $0 --backup /path/to/backup --confirm DESTROY_AND_RESTORE" >&2
}

while (($#)); do
  case "$1" in
    --backup) BACKUP_DIR="${2:-}"; shift 2 ;;
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$CONFIRM" == "DESTROY_AND_RESTORE" ]] || { echo "Explicit confirmation is required." >&2; usage; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "Missing restore configuration: $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${AOS_REPO_ROOT:?AOS_REPO_ROOT is required}"
: "${FRAPPE_BENCH_ROOT:?FRAPPE_BENCH_ROOT is required}"
: "${FRAPPE_SITE:?FRAPPE_SITE is required}"
DOCKER_COMPOSE_COMMAND="${DOCKER_COMPOSE_COMMAND:-docker compose}"

"$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR"

compose() { (cd "$AOS_REPO_ROOT" && bash -lc "$DOCKER_COMPOSE_COMMAND $*"); }
log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

restore_volume() {
  local volume="$1"
  local archive="$2"
  [[ -f "$archive" ]] || return 0
  log "Restoring Docker volume $volume"
  docker volume create "$volume" >/dev/null
  docker run --rm \
    --security-opt no-new-privileges:true \
    -v "${volume}:/target:rw" \
    -v "$(dirname "$archive"):/backup:ro" \
    alpine:3.21 \
    sh -c "rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; tar -xzf /backup/$(basename "$archive") -C /target"
}

log "Stopping Frappe and Docker infrastructure before restore"
(cd "$FRAPPE_BENCH_ROOT" && bench stop) 2>/dev/null || true
compose down

restore_volume aos_qdrant_data "$BACKUP_DIR/docker/qdrant_data.tar.gz"
restore_volume aos_minio_data "$BACKUP_DIR/docker/minio_data.tar.gz"
restore_volume aos_nominatim_data "$BACKUP_DIR/docker/nominatim_data.tar.gz"

if [[ -d "$BACKUP_DIR/maps" ]]; then
  log "Restoring map artifacts"
  for archive in "$BACKUP_DIR"/maps/*.tar.gz; do
    [[ -e "$archive" ]] || continue
    tar -xzf "$archive" -C "$AOS_REPO_ROOT"
  done
  [[ -f "$BACKUP_DIR/maps/manifest.env" ]] && cp -a "$BACKUP_DIR/maps/manifest.env" "$AOS_REPO_ROOT/infra/maps/manifest.env"
fi

DB_FILE="$(find "$BACKUP_DIR/frappe" -maxdepth 1 -type f \( -name '*database.sql.gz' -o -name '*database.sql' \) | head -1)"
[[ -n "$DB_FILE" ]] || { echo "Database backup not found." >&2; exit 1; }

PRIVATE_FILE="$(find "$BACKUP_DIR/frappe" -maxdepth 1 -type f -name '*private-files.tar*' | head -1 || true)"
PUBLIC_FILE="$(find "$BACKUP_DIR/frappe" -maxdepth 1 -type f -name '*files.tar*' ! -name '*private-files*' | head -1 || true)"

log "Restoring Frappe site $FRAPPE_SITE"
restore_args=(--site "$FRAPPE_SITE" restore "$DB_FILE" --force)
[[ -n "$PRIVATE_FILE" ]] && restore_args+=(--with-private-files "$PRIVATE_FILE")
[[ -n "$PUBLIC_FILE" ]] && restore_args+=(--with-public-files "$PUBLIC_FILE")
(cd "$FRAPPE_BENCH_ROOT" && bench "${restore_args[@]}")

log "Running migrations"
(cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" migrate)

log "Starting Docker infrastructure"
compose up -d
log "Restarting Frappe"
(cd "$FRAPPE_BENCH_ROOT" && bench restart)

log "Restore completed. Run the production verification checklist now."
