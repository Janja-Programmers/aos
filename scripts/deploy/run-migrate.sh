#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
: "${REMOTE_BENCH_ROOT:?REMOTE_BENCH_ROOT is required}"
: "${FRAPPE_SITE:?FRAPPE_SITE is required}"
[[ "$REMOTE_BENCH_ROOT" =~ ^/[A-Za-z0-9_./-]+$ ]] || { printf '[deploy] ERROR: Invalid REMOTE_BENCH_ROOT.\n' >&2; exit 2; }
[[ "$FRAPPE_SITE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,139}$ ]] || { printf '[deploy] ERROR: Invalid FRAPPE_SITE.\n' >&2; exit 2; }
AOS_MIGRATION_FAILURE_MARKER="${AOS_MIGRATION_FAILURE_MARKER:-/var/lib/aos/deployment/last-migration-failed.env}"
RELEASE_COMMIT="${RELEASE_COMMIT:-unknown}"

write_failure_marker() {
  local exit_code=$?
  trap - ERR
  local marker_dir marker_tmp
  marker_dir="$(dirname "$AOS_MIGRATION_FAILURE_MARKER")"
  install -d -m 0750 "$marker_dir"
  marker_tmp="$(mktemp "$marker_dir/.migration-failed.XXXXXX")"
  chmod 0600 "$marker_tmp"
  cat >"$marker_tmp" <<META
MIGRATION_FAILURE_VERSION=1
FAILED_AT_UTC=$(date -u +%FT%TZ)
FRAPPE_SITE=$FRAPPE_SITE
RELEASE_COMMIT=$RELEASE_COMMIT
ERROR_CATEGORY=MIGRATION_COMMAND_FAILED
META
  mv -f -- "$marker_tmp" "$AOS_MIGRATION_FAILURE_MARKER"
  exit "$exit_code"
}
trap write_failure_marker ERR

if [[ "$DRY_RUN" == "true" ]]; then
  printf '[deploy] Would run guarded migration for site %s and clear the failure marker only on success.\n' "$FRAPPE_SITE"
  exit 0
fi

cd "$REMOTE_BENCH_ROOT"
bench --site "$FRAPPE_SITE" migrate
rm -f -- "$AOS_MIGRATION_FAILURE_MARKER"
printf '[deploy] Migration completed and unresolved-failure marker is clear.\n'
