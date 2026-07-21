#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
BACKUP_INPUT=""
CONFIRM=""
TEMP_ROOT=""
RESTORE_STARTED_AT_UTC="$(date -u +%FT%TZ)"

usage() {
  echo "Usage: $0 --backup /path/to/backup-or-encrypted.age --confirm DESTROY_AND_RESTORE" >&2
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
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$CONFIRM" == "DESTROY_AND_RESTORE" ]] || { echo "Explicit confirmation is required." >&2; usage; exit 2; }
[[ -n "$BACKUP_INPUT" ]] || { usage; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "Missing restore configuration: $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${AOS_REPO_ROOT:?AOS_REPO_ROOT is required}"
: "${FRAPPE_BENCH_ROOT:?FRAPPE_BENCH_ROOT is required}"
: "${FRAPPE_SITE:?FRAPPE_SITE is required}"
: "${BACKUP_ROOT:?BACKUP_ROOT is required}"
DOCKER_COMPOSE_COMMAND="${DOCKER_COMPOSE_COMMAND:-docker compose}"
RESTORE_STATE_ROOT="${RESTORE_STATE_ROOT:-${BACKUP_ROOT}/restore-state}"
SITE_MARKER_NAME="${FRAPPE_SITE//[^a-zA-Z0-9._-]/_}"
RESTORE_STATE_MARKER="${RESTORE_STATE_MARKER:-${RESTORE_STATE_ROOT}/${SITE_MARKER_NAME}.env}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
compose() { (cd "$AOS_REPO_ROOT" && bash -lc "$DOCKER_COMPOSE_COMMAND $*"); }
metadata_value() { awk -F= -v key="$2" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$1"; }

BACKUP_DIR="$BACKUP_INPUT"
if [[ -f "$BACKUP_INPUT" ]]; then
  case "$BACKUP_INPUT" in
    *.age)
      TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aos-restore.XXXXXXXX")"
      chmod 700 "$TEMP_ROOT"
      log "Decrypting age-encrypted backup into a restricted temporary restore directory"
      BACKUP_DIR="$("$SCRIPT_DIR/backup_crypto.py" decrypt --input "$BACKUP_INPUT" --output-dir "$TEMP_ROOT")"
      ;;
    *) echo "Unsupported backup file. Supply a backup directory or an .age artifact." >&2; exit 2 ;;
  esac
fi
[[ -d "$BACKUP_DIR" ]] || { echo "Backup directory not found." >&2; exit 1; }
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

"$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR"

# Read NUL-delimited artifact selections without eval or word splitting.
mapfile -d '' -t discovered < <("$SCRIPT_DIR/backup_artifacts.py" discover "$BACKUP_DIR/frappe" --format null)
((${#discovered[@]} == 6)) || { echo "Unexpected backup artifact discovery output." >&2; exit 1; }
PUBLIC_FILE="${discovered[3]}"
PRIVATE_FILE="${discovered[5]}"

if [[ -z "$PUBLIC_FILE" && -z "$PRIVATE_FILE" ]]; then
  log "WARNING: DATABASE-ONLY RESTORE selected. No public or private file archive is present."
elif [[ -z "$PUBLIC_FILE" ]]; then
  log "WARNING: Public files archive is missing; only database and private files will be restored."
elif [[ -z "$PRIVATE_FILE" ]]; then
  log "WARNING: Private files archive is missing; only database and public files will be restored."
fi

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
    sh -c 'rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true; tar -xf "/backup/$1" -C /target' sh "$(basename "$archive")"
}

install -d -m 700 "$(dirname "$RESTORE_STATE_MARKER")"
rm -f -- "$RESTORE_STATE_MARKER"

log "Stopping Frappe and Docker infrastructure before restore"
(cd "$FRAPPE_BENCH_ROOT" && bench stop) 2>/dev/null || true
compose down

restore_volume aos_qdrant_data "$BACKUP_DIR/docker/qdrant_data.tar.gz"
restore_volume aos_minio_data "$BACKUP_DIR/docker/minio_data.tar.gz"
restore_volume aos_nominatim_data "$BACKUP_DIR/docker/nominatim_data.tar.gz"

if [[ -d "$BACKUP_DIR/maps" ]]; then
  log "Restoring map artifacts"
  while IFS= read -r -d '' archive; do
    tar -xf "$archive" -C "$AOS_REPO_ROOT"
  done < <(find "$BACKUP_DIR/maps" -maxdepth 1 -type f \( -name '*.tgz' -o -name '*.tar.gz' -o -name '*.tar' \) -print0)
  [[ -f "$BACKUP_DIR/maps/manifest.env" ]] && cp -a "$BACKUP_DIR/maps/manifest.env" "$AOS_REPO_ROOT/infra/maps/manifest.env"
fi

log "Restoring Frappe site $FRAPPE_SITE"
mapfile -d '' -t restore_args < <("$SCRIPT_DIR/backup_artifacts.py" restore-args "$BACKUP_DIR/frappe" --site "$FRAPPE_SITE" --format null)
(cd "$FRAPPE_BENCH_ROOT" && bench "${restore_args[@]}")
DB_RESULT="restored"
DB_RESTORED_AT_UTC="$(date -u +%FT%TZ)"

log "Running application migrations"
(cd "$FRAPPE_BENCH_ROOT" && bench --site "$FRAPPE_SITE" migrate)
MIGRATION_RESULT="passed"

SITE_ROOT="${FRAPPE_BENCH_ROOT}/sites/${FRAPPE_SITE}"
PUBLIC_RESULT="not_present"
PUBLIC_CHECKSUM="not_applicable"
PUBLIC_MEMBER="not_applicable"
PUBLIC_VERIFY_RESULT="not_present"
if [[ -n "$PUBLIC_FILE" ]]; then
  public_json="$("$SCRIPT_DIR/backup_artifacts.py" verify-restored-archive --archive "$PUBLIC_FILE" --site-root "$SITE_ROOT" --kind public)"
  PUBLIC_RESULT="$(python3 -c 'import json,sys; print("checksum_matched" if json.load(sys.stdin).get("ok") else "failed")' <<<"$public_json")"
  PUBLIC_CHECKSUM="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("actual_sha256") or "missing")' <<<"$public_json")"
  PUBLIC_MEMBER="$(python3 -c 'import json,sys,pathlib; print(pathlib.PurePosixPath(json.load(sys.stdin).get("member") or "missing").name)' <<<"$public_json")"
  [[ "$PUBLIC_RESULT" == "checksum_matched" ]] || { echo "Public file restoration verification failed." >&2; exit 1; }
  PUBLIC_VERIFY_RESULT="passed"
fi

PRIVATE_RESULT="not_present"
PRIVATE_CHECKSUM="not_applicable"
PRIVATE_MEMBER="not_applicable"
PRIVATE_VERIFY_RESULT="not_present"
if [[ -n "$PRIVATE_FILE" ]]; then
  private_json="$("$SCRIPT_DIR/backup_artifacts.py" verify-restored-archive --archive "$PRIVATE_FILE" --site-root "$SITE_ROOT" --kind private)"
  PRIVATE_RESULT="$(python3 -c 'import json,sys; print("checksum_matched" if json.load(sys.stdin).get("ok") else "failed")' <<<"$private_json")"
  PRIVATE_CHECKSUM="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("actual_sha256") or "missing")' <<<"$private_json")"
  PRIVATE_MEMBER="$(python3 -c 'import json,sys,pathlib; print(pathlib.PurePosixPath(json.load(sys.stdin).get("member") or "missing").name)' <<<"$private_json")"
  [[ "$PRIVATE_RESULT" == "checksum_matched" ]] || { echo "Private file restoration verification failed." >&2; exit 1; }
  PRIVATE_VERIFY_RESULT="passed"
fi

log "Starting Docker infrastructure"
compose up -d
log "Restarting Frappe"
(cd "$FRAPPE_BENCH_ROOT" && bench restart)

BACKUP_ID="$(basename "$BACKUP_DIR")"
BACKUP_CREATED_AT_UTC="$(metadata_value "$BACKUP_DIR/metadata.env" CREATED_AT_UTC)"
BACKUP_COMMIT="$(metadata_value "$BACKUP_DIR/metadata.env" AOS_GIT_COMMIT)"
RESTORE_COMPLETED_AT_UTC="$(date -u +%FT%TZ)"
marker_tmp="$(mktemp "$(dirname "$RESTORE_STATE_MARKER")/.restore-state.XXXXXX")"
chmod 600 "$marker_tmp"
cat > "$marker_tmp" <<MARKER
RESTORE_STATE_VERSION=2
RESTORE_ENVIRONMENT=${AOS_ENVIRONMENT:-unknown}
FRAPPE_SITE=$FRAPPE_SITE
BACKUP_ID=$BACKUP_ID
BACKUP_CREATED_AT_UTC=$BACKUP_CREATED_AT_UTC
BACKUP_GIT_COMMIT=${BACKUP_COMMIT:-unknown}
RESTORE_STARTED_AT_UTC=$RESTORE_STARTED_AT_UTC
DB_RESTORED_AT_UTC=$DB_RESTORED_AT_UTC
RESTORE_COMPLETED_AT_UTC=$RESTORE_COMPLETED_AT_UTC
DATABASE_RESTORE_RESULT=$DB_RESULT
PUBLIC_ARCHIVE_PRESENT=$([[ -n "$PUBLIC_FILE" ]] && echo true || echo false)
PUBLIC_FILES_PRESENT=$([[ -n "$PUBLIC_FILE" ]] && echo true || echo false)
PUBLIC_FILES_RESTORE_RESULT=$PUBLIC_RESULT
PUBLIC_REPRESENTATIVE_FILE=$PUBLIC_MEMBER
PUBLIC_REPRESENTATIVE_SHA256=$PUBLIC_CHECKSUM
PUBLIC_REPRESENTATIVE_VERIFY_RESULT=$PUBLIC_VERIFY_RESULT
PRIVATE_ARCHIVE_PRESENT=$([[ -n "$PRIVATE_FILE" ]] && echo true || echo false)
PRIVATE_FILES_PRESENT=$([[ -n "$PRIVATE_FILE" ]] && echo true || echo false)
PRIVATE_FILES_RESTORE_RESULT=$PRIVATE_RESULT
PRIVATE_REPRESENTATIVE_FILE=$PRIVATE_MEMBER
PRIVATE_REPRESENTATIVE_SHA256=$PRIVATE_CHECKSUM
PRIVATE_REPRESENTATIVE_VERIFY_RESULT=$PRIVATE_VERIFY_RESULT
MIGRATION_RESULT=$MIGRATION_RESULT
CHECKSUM_VERIFICATION_RESULT=passed
MARKER
mv -f -- "$marker_tmp" "$RESTORE_STATE_MARKER"

log "Restore completed with database/public/private evidence recorded at $RESTORE_STATE_MARKER"
trap - EXIT INT TERM
cleanup
