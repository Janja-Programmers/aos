#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
BACKUP_DIR="${1:-}"

[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || { echo "Usage: $0 /path/to/verified-backup" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "Missing backup configuration: $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

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

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
is_true() { [[ "${1,,}" == "true" || "$1" == "1" || "${1,,}" == "yes" || "${1,,}" == "on" ]]; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }

backup_id="$(basename "$BACKUP_DIR")"
mode="${OFFSITE_BACKUP_MODE,,}"
if [[ -z "$mode" ]]; then
  if [[ -n "$REMOTE_COPY_COMMAND" ]]; then
    mode="custom"
  elif is_true "${OFFSITE_BACKUP_ENABLED:-false}"; then
    mode="rsync"
  else
    mode="disabled"
  fi
fi

if [[ "$mode" == "disabled" || "$mode" == "none" ]]; then
  log "Offsite backup is disabled; skipping remote copy."
  exit 0
fi

[[ -f "$BACKUP_DIR/VERIFIED_AT_UTC" ]] || {
  echo "Refusing offsite copy because backup has no VERIFIED_AT_UTC marker: $BACKUP_DIR" >&2
  exit 1
}
"$SCRIPT_DIR/verify-backup.sh" "$BACKUP_DIR" >/dev/null

case "$mode" in
  rsync)
    require_cmd rsync
    [[ -n "$OFFSITE_RSYNC_TARGET" ]] || { echo "OFFSITE_RSYNC_TARGET is required for OFFSITE_BACKUP_MODE=rsync" >&2; exit 1; }
    target="${OFFSITE_RSYNC_TARGET%/}/${backup_id}/"
    rsync_args=(-a --partial --protect-args)
    if [[ -n "$OFFSITE_RSYNC_EXTRA_ARGS" ]]; then
      # shellcheck disable=SC2206
      extra_args=($OFFSITE_RSYNC_EXTRA_ARGS)
      rsync_args+=("${extra_args[@]}")
    fi
    if [[ -n "$OFFSITE_RSYNC_SSH_KEY" ]]; then
      [[ -f "$OFFSITE_RSYNC_SSH_KEY" ]] || { echo "OFFSITE_RSYNC_SSH_KEY not found: $OFFSITE_RSYNC_SSH_KEY" >&2; exit 1; }
      rsync_args+=(-e "ssh -i $OFFSITE_RSYNC_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    fi
    log "Copying verified backup offsite with rsync."
    rsync "${rsync_args[@]}" "$BACKUP_DIR/" "$target"
    ;;
  s3)
    require_cmd aws
    [[ -n "$OFFSITE_S3_BUCKET" ]] || { echo "OFFSITE_S3_BUCKET is required for OFFSITE_BACKUP_MODE=s3" >&2; exit 1; }
    prefix="${OFFSITE_S3_PREFIX#/}"
    prefix="${prefix%/}"
    if [[ -n "$prefix" ]]; then
      destination="s3://${OFFSITE_S3_BUCKET}/${prefix}/${backup_id}/"
    else
      destination="s3://${OFFSITE_S3_BUCKET}/${backup_id}/"
    fi
    aws_args=()
    [[ -n "$OFFSITE_S3_ENDPOINT_URL" ]] && aws_args+=(--endpoint-url "$OFFSITE_S3_ENDPOINT_URL")
    [[ -n "$OFFSITE_S3_STORAGE_CLASS" ]] && aws_args+=(--storage-class "$OFFSITE_S3_STORAGE_CLASS")
    if [[ -n "$OFFSITE_S3_EXTRA_ARGS" ]]; then
      # shellcheck disable=SC2206
      extra_args=($OFFSITE_S3_EXTRA_ARGS)
      aws_args+=("${extra_args[@]}")
    fi
    log "Copying verified backup offsite with aws s3 sync."
    aws "${aws_args[@]}" s3 sync "$BACKUP_DIR" "$destination" --only-show-errors
    ;;
  custom)
    [[ -n "$REMOTE_COPY_COMMAND" ]] || { echo "REMOTE_COPY_COMMAND is required for OFFSITE_BACKUP_MODE=custom" >&2; exit 1; }
    log "Copying verified backup offsite with REMOTE_COPY_COMMAND."
    "$REMOTE_COPY_COMMAND" "$BACKUP_DIR"
    ;;
  *)
    echo "Unsupported OFFSITE_BACKUP_MODE: $mode. Use disabled, rsync, s3, or custom." >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "$OFFSITE_SYNC_MARKER")"
cat > "$OFFSITE_SYNC_MARKER" <<MARKER
OFFSITE_SYNC_PASSED_AT_UTC=$(date -u +%FT%TZ)
OFFSITE_BACKUP_ID=$backup_id
OFFSITE_BACKUP_MODE=$mode
MARKER
chmod 600 "$OFFSITE_SYNC_MARKER" 2>/dev/null || true
date -u +%FT%TZ > "$BACKUP_DIR/OFFSITE_SYNCED_AT_UTC"
log "Offsite backup completed for $backup_id. Marker: $OFFSITE_SYNC_MARKER"
