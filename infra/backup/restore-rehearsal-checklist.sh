#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
BACKUP_INPUT=""
MARK_PASSED="false"
RUN_TESTS="false"
TEMP_ROOT=""

usage() {
  cat >&2 <<USAGE
Usage: $0 --backup /path/to/backup-or-encrypted.age [--mark-passed] [--run-tests]

Run after restore.sh has restored the exact selected backup into an isolated
staging, test, or rehearsal site. Full file evidence is mandatory whenever
RESTORE_REHEARSAL_REQUIRE_FILES=true. Database-only rehearsal requires the
explicit RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY=true policy.
USAGE
}

cleanup() {
  local code=$?
  if [[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" ]]; then
    chmod -R u+rwX,go-rwx "$TEMP_ROOT" 2>/dev/null || true
    rm -rf -- "$TEMP_ROOT"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

while (($#)); do
  case "$1" in
    --backup) BACKUP_INPUT="${2:-}"; shift 2 ;;
    --mark-passed) MARK_PASSED="true"; shift ;;
    --run-tests) RUN_TESTS="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$BACKUP_INPUT" ]] || { usage; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "Missing backup configuration: $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${FRAPPE_BENCH_ROOT:?FRAPPE_BENCH_ROOT is required}"
: "${FRAPPE_SITE:?FRAPPE_SITE is required}"
: "${BACKUP_ROOT:?BACKUP_ROOT is required}"

RESTORE_REHEARSAL_ENVIRONMENT="${RESTORE_REHEARSAL_ENVIRONMENT:-rehearsal}"
case "${RESTORE_REHEARSAL_ENVIRONMENT,,}" in
  rehearsal|staging|test) ;;
  *) echo "RESTORE_REHEARSAL_ENVIRONMENT must be rehearsal, staging, or test; never production." >&2; exit 1 ;;
esac
RESTORE_REHEARSAL_MARKER="${RESTORE_REHEARSAL_MARKER:-${BACKUP_ROOT}/restore-rehearsal-passed.env}"
RESTORE_STATE_ROOT="${RESTORE_STATE_ROOT:-${BACKUP_ROOT}/restore-state}"
SITE_MARKER_NAME="${FRAPPE_SITE//[^a-zA-Z0-9._-]/_}"
RESTORE_STATE_MARKER="${RESTORE_STATE_MARKER:-${RESTORE_STATE_ROOT}/${SITE_MARKER_NAME}.env}"
RESTORE_REHEARSAL_REQUIRE_FILES="${RESTORE_REHEARSAL_REQUIRE_FILES:-true}"
RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY="${RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY:-false}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
is_true() { [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]; }
env_value() { awk -F= -v key="$2" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$1"; }
run_bench_execute() {
  local method="$1"
  log "Running $method"
  (cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" execute "$method")
}

BACKUP_DIR="$BACKUP_INPUT"
if [[ -f "$BACKUP_INPUT" ]]; then
  case "$BACKUP_INPUT" in
    *.age)
      TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aos-rehearsal.XXXXXXXX")"
      chmod 700 "$TEMP_ROOT"
      log "Decrypting encrypted backup into a restricted temporary rehearsal directory"
      BACKUP_DIR="$("$SCRIPT_DIR/backup_crypto.py" decrypt --input "$BACKUP_INPUT" --output-dir "$TEMP_ROOT")"
      ;;
    *) echo "Unsupported rehearsal backup file; use a directory or .age artifact." >&2; exit 2 ;;
  esac
fi
[[ -d "$BACKUP_DIR" ]] || { echo "Backup directory is unavailable." >&2; exit 1; }
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
BACKUP_ID="$(basename "$BACKUP_DIR")"

[[ -f "$RESTORE_STATE_MARKER" ]] || { echo "Restore state marker is missing; run restore.sh first." >&2; exit 1; }
[[ "$(env_value "$RESTORE_STATE_MARKER" BACKUP_ID)" == "$BACKUP_ID" ]] || { echo "Restore state marker refers to a different backup." >&2; exit 1; }
[[ "$(env_value "$RESTORE_STATE_MARKER" DATABASE_RESTORE_RESULT)" == "restored" ]] || { echo "Database restore evidence is missing." >&2; exit 1; }
[[ "$(env_value "$RESTORE_STATE_MARKER" MIGRATION_RESULT)" == "passed" ]] || { echo "Migration evidence is missing from restore state." >&2; exit 1; }

restore_completed="$(env_value "$RESTORE_STATE_MARKER" RESTORE_COMPLETED_AT_UTC)"
db_restored="$(env_value "$RESTORE_STATE_MARKER" DB_RESTORED_AT_UTC)"
[[ -n "$restore_completed" && -n "$db_restored" ]] || { echo "Restore completion timestamps are missing." >&2; exit 1; }
python3 - "$db_restored" "$restore_completed" <<'PY'
from datetime import datetime
import sys
parse=lambda v: datetime.fromisoformat(v.replace('Z','+00:00'))
if parse(sys.argv[2]) < parse(sys.argv[1]):
    raise SystemExit("Restore completion marker predates database restore completion.")
PY

log "Verifying the exact backup artifact used for rehearsal"
"$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR"
POLICY_JSON="$(python3 "$SCRIPT_DIR/rehearsal_policy.py" \
  --backup-dir "$BACKUP_DIR" \
  --restore-state "$RESTORE_STATE_MARKER" \
  --require-files "$RESTORE_REHEARSAL_REQUIRE_FILES" \
  --allow-database-only "$RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY")"
POLICY_MODE="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["mode"])' <<<"$POLICY_JSON")"
mapfile -d '' -t discovered < <("$SCRIPT_DIR/backup_artifacts.py" discover "$BACKUP_DIR/frappe" --format null)
((${#discovered[@]} == 6)) || { echo "Unexpected backup artifact discovery output." >&2; exit 1; }
PUBLIC_FILE="${discovered[3]}"
PRIVATE_FILE="${discovered[5]}"
PUBLIC_PRESENT=$([[ -n "$PUBLIC_FILE" ]] && echo true || echo false)
PRIVATE_PRESENT=$([[ -n "$PRIVATE_FILE" ]] && echo true || echo false)
PUBLIC_RESULT="$(env_value "$RESTORE_STATE_MARKER" PUBLIC_FILES_RESTORE_RESULT)"
PRIVATE_RESULT="$(env_value "$RESTORE_STATE_MARKER" PRIVATE_FILES_RESTORE_RESULT)"
PUBLIC_VERIFY_RESULT="$(env_value "$RESTORE_STATE_MARKER" PUBLIC_REPRESENTATIVE_VERIFY_RESULT)"
PRIVATE_VERIFY_RESULT="$(env_value "$RESTORE_STATE_MARKER" PRIVATE_REPRESENTATIVE_VERIFY_RESULT)"
[[ -n "$PUBLIC_VERIFY_RESULT" ]] || PUBLIC_VERIFY_RESULT=$([[ "$PUBLIC_RESULT" == "checksum_matched" ]] && echo passed || echo not_present)
[[ -n "$PRIVATE_VERIFY_RESULT" ]] || PRIVATE_VERIFY_RESULT=$([[ "$PRIVATE_RESULT" == "checksum_matched" ]] && echo passed || echo not_present)

REHEARSAL_MODE="$POLICY_MODE"
if [[ -z "$PUBLIC_FILE" && -z "$PRIVATE_FILE" ]]; then
  REHEARSAL_MODE="database-only"
fi
if is_true "$RESTORE_REHEARSAL_REQUIRE_FILES"; then
  [[ -n "$PUBLIC_FILE" ]] || { echo "Public files archive is required for this rehearsal." >&2; exit 1; }
  [[ -n "$PRIVATE_FILE" ]] || { echo "Private files archive is required for this rehearsal." >&2; exit 1; }
  [[ "$PUBLIC_RESULT" == "checksum_matched" && "$PUBLIC_VERIFY_RESULT" == "passed" ]] || { echo "Public restored-file checksum evidence is incomplete." >&2; exit 1; }
  [[ "$PRIVATE_RESULT" == "checksum_matched" && "$PRIVATE_VERIFY_RESULT" == "passed" ]] || { echo "Private restored-file checksum evidence is incomplete." >&2; exit 1; }
  REHEARSAL_MODE="full"
else
  if [[ "$REHEARSAL_MODE" == "database-only" ]]; then
    is_true "$RESTORE_REHEARSAL_ALLOW_DATABASE_ONLY" || { echo "Database-only rehearsal was not explicitly allowed." >&2; exit 1; }
    [[ "$PUBLIC_RESULT" == "not_present" && "$PRIVATE_RESULT" == "not_present" ]] || { echo "Database-only evidence conflicts with restore state." >&2; exit 1; }
  else
    [[ -z "$PUBLIC_FILE" || "$PUBLIC_RESULT" == "checksum_matched" ]] || { echo "Public restoration evidence is invalid." >&2; exit 1; }
    [[ -z "$PRIVATE_FILE" || "$PRIVATE_RESULT" == "checksum_matched" ]] || { echo "Private restoration evidence is invalid." >&2; exit 1; }
  fi
fi

log "Re-running migrations on the restored application"
(cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" migrate)
MIGRATION_RESULT="passed"
run_bench_execute aos.utils.production_config.assert_restore_rehearsal_config_ready
PRODUCTION_CONFIG_RESULT="passed"
run_bench_execute aos.utils.operational_health.assert_operational_health_ready
HEALTH_CHECK_RESULT="passed"
run_bench_execute aos.utils.job_monitoring.assert_job_monitoring_ready
JOB_MONITORING_RESULT="passed"

if [[ "$RUN_TESTS" == "true" ]]; then
  log "Running AOS backend tests on the restored site"
  (cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" run-tests --app aos)
  TEST_RESULT="passed"
else
  TEST_RESULT="not_requested"
fi

if [[ "$MARK_PASSED" == "true" ]]; then
  log "Writing restore rehearsal marker atomically after all required validation passed"
  install -d -m 700 "$(dirname "$RESTORE_REHEARSAL_MARKER")"
  marker_tmp="$(mktemp "$(dirname "$RESTORE_REHEARSAL_MARKER")/.restore-rehearsal.XXXXXX")"
  chmod 600 "$marker_tmp"
  BACKUP_CREATED_AT_UTC="$(env_value "$BACKUP_DIR/metadata.env" CREATED_AT_UTC)"
  BACKUP_GIT_COMMIT="$(env_value "$BACKUP_DIR/metadata.env" AOS_GIT_COMMIT)"
  cat > "$marker_tmp" <<META
RESTORE_REHEARSAL_VERSION=3
RESTORE_REHEARSAL_ENVIRONMENT=$RESTORE_REHEARSAL_ENVIRONMENT
RESTORE_REHEARSAL_MODE=$REHEARSAL_MODE
RESTORE_REHEARSAL_PASSED_AT_UTC=$(date -u +%FT%TZ)
FRAPPE_SITE=$FRAPPE_SITE
BACKUP_ID=$BACKUP_ID
BACKUP_CREATED_AT_UTC=$BACKUP_CREATED_AT_UTC
BACKUP_GIT_COMMIT=${BACKUP_GIT_COMMIT:-unknown}
RESTORE_COMPLETED_AT_UTC=$restore_completed
DATABASE_RESTORE_RESULT=restored
PUBLIC_ARCHIVE_PRESENT=$PUBLIC_PRESENT
PUBLIC_FILES_RESTORE_RESULT=$PUBLIC_RESULT
PUBLIC_REPRESENTATIVE_FILE_VERIFY_RESULT=$PUBLIC_VERIFY_RESULT
PRIVATE_ARCHIVE_PRESENT=$PRIVATE_PRESENT
PRIVATE_FILES_RESTORE_RESULT=$PRIVATE_RESULT
PRIVATE_REPRESENTATIVE_FILE_VERIFY_RESULT=$PRIVATE_VERIFY_RESULT
CHECKSUM_VERIFICATION_RESULT=passed
MIGRATION_RESULT=$MIGRATION_RESULT
PRODUCTION_CONFIG_RESULT=$PRODUCTION_CONFIG_RESULT
HEALTH_CHECK_RESULT=$HEALTH_CHECK_RESULT
JOB_MONITORING_RESULT=$JOB_MONITORING_RESULT
TEST_RESULT=$TEST_RESULT
META
  mv -f -- "$marker_tmp" "$RESTORE_REHEARSAL_MARKER"
fi

log "Restore rehearsal checks completed for backup $BACKUP_ID in $REHEARSAL_MODE mode."
cleanup
