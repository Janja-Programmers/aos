#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
SOURCE="${1:-}"
ENCRYPTED_ARTIFACT="${2:-}"

[[ -n "$SOURCE" ]] || { echo "Usage: $0 /path/to/encrypted-backup.age OR /path/to/verified-backup [encrypted-artifact]" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "Missing backup configuration: $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${BACKUP_ROOT:?BACKUP_ROOT is required}"

OFFSITE_BACKUP_MODE="${OFFSITE_BACKUP_MODE:-}"
OFFSITE_BACKUP_ENABLED="${OFFSITE_BACKUP_ENABLED:-}"
OFFSITE_SYNC_MARKER="${OFFSITE_SYNC_MARKER:-${BACKUP_ROOT}/offsite-sync-passed.env}"
REMOTE_COPY_COMMAND="${REMOTE_COPY_COMMAND:-}"
OFFSITE_RSYNC_TARGET="${OFFSITE_RSYNC_TARGET:-}"
OFFSITE_RSYNC_SSH_KEY="${OFFSITE_RSYNC_SSH_KEY:-}"
OFFSITE_RSYNC_EXTRA_ARGS="${OFFSITE_RSYNC_EXTRA_ARGS:-}"
OFFSITE_S3_BUCKET="${OFFSITE_S3_BUCKET:-}"
OFFSITE_S3_PREFIX="${OFFSITE_S3_PREFIX:-aos-backups}"
OFFSITE_S3_ENDPOINT_URL="${OFFSITE_S3_ENDPOINT_URL:-}"
OFFSITE_S3_STORAGE_CLASS="${OFFSITE_S3_STORAGE_CLASS:-}"
OFFSITE_S3_EXTRA_ARGS="${OFFSITE_S3_EXTRA_ARGS:-}"
BACKUP_ENCRYPTION_METHOD="${BACKUP_ENCRYPTION_METHOD:-none}"
ENCRYPTED_BACKUP_ROOT="${ENCRYPTED_BACKUP_ROOT:-${BACKUP_ROOT}/encrypted}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
is_true() { [[ "${1,,}" == "true" || "$1" == "1" || "${1,,}" == "yes" || "${1,,}" == "on" ]]; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }

mode="${OFFSITE_BACKUP_MODE,,}"
if [[ -z "$mode" ]]; then
  if [[ -n "$REMOTE_COPY_COMMAND" ]]; then mode="custom";
  elif is_true "${OFFSITE_BACKUP_ENABLED:-false}"; then mode="rsync";
  else mode="disabled"; fi
fi

if [[ "$mode" == "disabled" || "$mode" == "none" ]]; then
  log "Offsite backup is disabled; skipping remote copy."
  exit 0
fi

"$SCRIPT_DIR/backup_crypto.py" validate >/dev/null

encryption_method="${BACKUP_ENCRYPTION_METHOD,,}"
BACKUP_DIR=""
copy_kind="plaintext-development-only"
if [[ "$encryption_method" == "age" ]]; then
  if [[ -f "$SOURCE" ]]; then
    ENCRYPTED_ARTIFACT="$SOURCE"
  elif [[ -d "$SOURCE" ]]; then
    BACKUP_DIR="$SOURCE"
    backup_id="$(basename "$BACKUP_DIR")"
    [[ -n "$ENCRYPTED_ARTIFACT" ]] || ENCRYPTED_ARTIFACT="${ENCRYPTED_BACKUP_ROOT}/${backup_id}.tar.gz.age"
  else
    echo "Encrypted backup artifact or temporary verified backup directory was not found." >&2
    exit 1
  fi
  "$SCRIPT_DIR/backup_crypto.py" verify --input "$ENCRYPTED_ARTIFACT" >/dev/null
  copy_source="$ENCRYPTED_ARTIFACT"
  copy_kind="encrypted-age"
  encrypted_sha256="$(awk 'NF {print $1; exit}' "${ENCRYPTED_ARTIFACT}.sha256")"
  [[ "$encrypted_sha256" =~ ^[0-9a-f]{64}$ ]] || { echo "Encrypted backup checksum sidecar is invalid." >&2; exit 1; }
  metadata="${ENCRYPTED_ARTIFACT}.metadata.env"
  backup_id=""
  if [[ -f "$metadata" ]]; then
    backup_id="$(sed -n 's/^BACKUP_ID=//p' "$metadata" | head -1)"
  fi
  [[ "$backup_id" =~ ^[A-Za-z0-9._-]+$ ]] || backup_id="$(basename "$ENCRYPTED_ARTIFACT" .tar.gz.age)"
else
  [[ -d "$SOURCE" ]] || {
    echo "Development plaintext offsite copy requires a verified backup directory." >&2
    exit 1
  }
  BACKUP_DIR="$SOURCE"
  backup_id="$(basename "$BACKUP_DIR")"
  [[ -f "$BACKUP_DIR/VERIFIED_AT_UTC" ]] || {
    echo "Refusing offsite copy because backup has no VERIFIED_AT_UTC marker: $BACKUP_DIR" >&2
    exit 1
  }
  "$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR" >/dev/null
  copy_source="$BACKUP_DIR"
  # backup_crypto.py validate already rejects plaintext in production/required mode.
  log "WARNING: copying plaintext backup is permitted only for non-production development environments."
fi

copy_encrypted_sidecars_rsync() {
  local target="$1"
  rsync "${rsync_args[@]}" "$copy_source" "$copy_source.sha256" "$copy_source.metadata.env" "$target"
}

case "$mode" in
  rsync)
    require_cmd rsync
    [[ -n "$OFFSITE_RSYNC_TARGET" ]] || { echo "OFFSITE_RSYNC_TARGET is required for OFFSITE_BACKUP_MODE=rsync" >&2; exit 1; }
    rsync_args=(-a --partial --protect-args)
    if [[ -n "$OFFSITE_RSYNC_EXTRA_ARGS" ]]; then
      # shellcheck disable=SC2206
      extra_args=($OFFSITE_RSYNC_EXTRA_ARGS)
      rsync_args+=("${extra_args[@]}")
    fi
    if [[ -n "$OFFSITE_RSYNC_SSH_KEY" ]]; then
      [[ -f "$OFFSITE_RSYNC_SSH_KEY" ]] || { echo "OFFSITE_RSYNC_SSH_KEY not found." >&2; exit 1; }
      rsync_args+=(-e "ssh -i $OFFSITE_RSYNC_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    fi
    target="${OFFSITE_RSYNC_TARGET%/}/${backup_id}/"
    log "Copying verified ${copy_kind} backup offsite with rsync."
    if [[ "$encryption_method" == "age" ]]; then
      copy_encrypted_sidecars_rsync "$target"
    else
      rsync "${rsync_args[@]}" "$BACKUP_DIR/" "$target"
    fi
    ;;
  s3)
    require_cmd aws
    [[ -n "$OFFSITE_S3_BUCKET" ]] || { echo "OFFSITE_S3_BUCKET is required for OFFSITE_BACKUP_MODE=s3" >&2; exit 1; }
    prefix="${OFFSITE_S3_PREFIX#/}"; prefix="${prefix%/}"
    destination="s3://${OFFSITE_S3_BUCKET}/${prefix:+${prefix}/}${backup_id}/"
    aws_args=()
    [[ -n "$OFFSITE_S3_ENDPOINT_URL" ]] && aws_args+=(--endpoint-url "$OFFSITE_S3_ENDPOINT_URL")
    [[ -n "$OFFSITE_S3_STORAGE_CLASS" ]] && aws_args+=(--storage-class "$OFFSITE_S3_STORAGE_CLASS")
    if [[ -n "$OFFSITE_S3_EXTRA_ARGS" ]]; then
      # shellcheck disable=SC2206
      extra_args=($OFFSITE_S3_EXTRA_ARGS); aws_args+=("${extra_args[@]}")
    fi
    log "Copying verified ${copy_kind} backup offsite with aws s3."
    if [[ "$encryption_method" == "age" ]]; then
      aws "${aws_args[@]}" s3 cp "$copy_source" "${destination}$(basename "$copy_source")" --only-show-errors
      aws "${aws_args[@]}" s3 cp "$copy_source.sha256" "${destination}$(basename "$copy_source.sha256")" --only-show-errors
      aws "${aws_args[@]}" s3 cp "$copy_source.metadata.env" "${destination}$(basename "$copy_source.metadata.env")" --only-show-errors
    else
      aws "${aws_args[@]}" s3 sync "$BACKUP_DIR" "$destination" --only-show-errors
    fi
    ;;
  custom)
    [[ -n "$REMOTE_COPY_COMMAND" ]] || { echo "REMOTE_COPY_COMMAND is required for OFFSITE_BACKUP_MODE=custom" >&2; exit 1; }
    log "Copying verified ${copy_kind} backup with the audited custom command."
    "$REMOTE_COPY_COMMAND" "$copy_source"
    ;;
  *) echo "Unsupported OFFSITE_BACKUP_MODE: $mode. Use disabled, rsync, s3, or custom." >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$OFFSITE_SYNC_MARKER")"
cat > "$OFFSITE_SYNC_MARKER" <<MARKER
OFFSITE_SYNC_PASSED_AT_UTC=$(date -u +%FT%TZ)
OFFSITE_BACKUP_ID=$backup_id
OFFSITE_BACKUP_MODE=$mode
OFFSITE_ENCRYPTION_METHOD=$encryption_method
OFFSITE_ARTIFACT_KIND=$copy_kind
OFFSITE_ARTIFACT_NAME=$(basename "$copy_source")
OFFSITE_ENCRYPTED_SHA256=${encrypted_sha256:-}
MARKER
chmod 600 "$OFFSITE_SYNC_MARKER" 2>/dev/null || true
if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
  date -u +%FT%TZ > "$BACKUP_DIR/OFFSITE_SYNCED_AT_UTC"
fi
log "Offsite backup completed for $backup_id using encryption method $encryption_method."
