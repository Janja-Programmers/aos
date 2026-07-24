#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ENV_FILE="${AOS_BACKUP_ENV_FILE:-/etc/aos/backup.env}"
UNIT_NAME="${1:-aos-backup.service}"
[[ -r "${ENV_FILE}" ]] || { echo "Backup failure notification configuration is unavailable." >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

METHOD="${BACKUP_FAILURE_NOTIFICATION_METHOD:-none}"
MESSAGE="AOS backup unit ${UNIT_NAME} failed on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ). See journalctl for the unit."
case "${METHOD}" in
  none)
    if [[ "${AOS_ENVIRONMENT:-development}" == "production" && "${BACKUP_FAILURE_NOTIFICATIONS_REQUIRED:-true}" == "true" ]]; then
      echo "Backup failure notification is required but not configured." >&2
      exit 1
    fi
    echo "Backup failure notification disabled for this environment." >&2
    ;;
  webhook)
    [[ -n "${BACKUP_FAILURE_WEBHOOK_URL:-}" ]] || { echo "Backup failure webhook is not configured." >&2; exit 1; }
    command -v curl >/dev/null 2>&1 || { echo "curl is required for webhook notification." >&2; exit 1; }
    curl --fail --silent --show-error --max-time 15 \
      --header 'Content-Type: application/json' \
      --data "$(python3 -c 'import json,sys; print(json.dumps({"text":sys.argv[1]}))' "${MESSAGE}")" \
      "${BACKUP_FAILURE_WEBHOOK_URL}" >/dev/null
    ;;
  email)
    [[ -n "${BACKUP_FAILURE_EMAIL_TO:-}" ]] || { echo "Backup failure email recipient is not configured." >&2; exit 1; }
    command -v sendmail >/dev/null 2>&1 || { echo "sendmail is required for email notification." >&2; exit 1; }
    printf 'To: %s\nSubject: AOS backup failure\n\n%s\n' "${BACKUP_FAILURE_EMAIL_TO}" "${MESSAGE}" | sendmail -t
    ;;
  *)
    echo "Unsupported backup failure notification method." >&2
    exit 1
    ;;
esac
