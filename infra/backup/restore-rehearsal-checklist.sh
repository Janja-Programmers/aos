#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
BACKUP_DIR=""
MARK_PASSED="false"
RUN_TESTS="false"

usage() {
  cat >&2 <<USAGE
Usage: $0 [--backup /path/to/backup] [--mark-passed] [--run-tests]

Run this after restoring an AOS backup into a clean staging/test site. It checks
backup integrity and the restored app's production config, operational health,
and job monitoring diagnostics. With --mark-passed, it writes a restore
rehearsal marker consumed by aos.utils.backup_readiness.
USAGE
}

while (($#)); do
  case "$1" in
    --backup) BACKUP_DIR="${2:-}"; shift 2 ;;
    --mark-passed) MARK_PASSED="true"; shift ;;
    --run-tests) RUN_TESTS="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -f "$ENV_FILE" ]] || { echo "Missing backup configuration: $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${FRAPPE_BENCH_ROOT:?FRAPPE_BENCH_ROOT is required}"
: "${FRAPPE_SITE:?FRAPPE_SITE is required}"
: "${BACKUP_ROOT:?BACKUP_ROOT is required}"

RESTORE_REHEARSAL_MARKER="${RESTORE_REHEARSAL_MARKER:-${BACKUP_ROOT}/restore-rehearsal-passed.env}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
run_bench_execute() {
  local method="$1"
  log "Running $method"
  (cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" execute "$method")
}

if [[ -n "$BACKUP_DIR" ]]; then
  log "Verifying backup artifact before restored-site checks"
  "$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR"
fi

run_bench_execute aos.utils.production_config.production_config_summary
run_bench_execute aos.utils.operational_health.operational_health_summary
run_bench_execute aos.utils.job_monitoring.job_monitoring_summary

if [[ "$RUN_TESTS" == "true" ]]; then
  log "Running AOS backend tests on restored site"
  (cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" run-tests --app aos)
fi

if [[ "$MARK_PASSED" == "true" ]]; then
  log "Writing restore rehearsal marker"
  install -d -m 700 "$(dirname "$RESTORE_REHEARSAL_MARKER")"
  cat > "$RESTORE_REHEARSAL_MARKER" <<META
RESTORE_REHEARSAL_PASSED_AT_UTC=$(date -u +%FT%TZ)
FRAPPE_SITE=$FRAPPE_SITE
BACKUP_BASENAME=$(basename "${BACKUP_DIR:-manual}")
META
  chmod 600 "$RESTORE_REHEARSAL_MARKER"
fi

log "Restore rehearsal checks completed."
