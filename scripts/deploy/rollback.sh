#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
DRY_RUN=false; [[ "${1:-}" == --dry-run ]] && DRY_RUN=true
validate_environment
require_var ROLLBACK_COMMIT; [[ "$ROLLBACK_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die 'ROLLBACK_COMMIT must be the exact prior Git SHA.'
require_var ROLLBACK_MANIFEST; [[ -f "$ROLLBACK_MANIFEST" ]] || die 'Prior release manifest is missing.'
python3 scripts/deploy/release_manifest.py verify-manifest "$ROLLBACK_MANIFEST" "$ROLLBACK_COMMIT"
require_var VERIFIED_BACKUP_ID
if [[ "$DRY_RUN" == true ]]; then log "Would roll back to ${ROLLBACK_COMMIT} using manifest and verified backup ${VERIFIED_BACKUP_ID}."; exit 0; fi
require_var DEPLOY_KNOWN_HOSTS_FILE; require_var REMOTE_ROLLBACK_COMMAND
prepare_ssh; trap cleanup_ssh EXIT
remote "ROLLBACK_COMMIT='$ROLLBACK_COMMIT' VERIFIED_BACKUP_ID='$VERIFIED_BACKUP_ID' $REMOTE_ROLLBACK_COMMAND"
log "Rollback command completed for exact prior commit ${ROLLBACK_COMMIT}."
