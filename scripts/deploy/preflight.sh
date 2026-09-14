#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
DRY_RUN=false; [[ "${1:-}" == --dry-run ]] && DRY_RUN=true
validate_environment; validate_release
[[ "${CI_GATE_VERIFIED:-false}" == true ]] || { [[ "$DRY_RUN" == true ]] || die 'Required CI gate was not verified.'; }
if [[ "$DEPLOY_ENVIRONMENT" == production ]]; then
  [[ "${PRODUCTION_APPROVAL_CONTEXT:-false}" == true ]] || { [[ "$DRY_RUN" == true ]] || die 'Production approval context is missing.'; }
fi
if [[ "$DRY_RUN" == true ]]; then
  log "Dry-run preflight valid for ${DEPLOY_ENVIRONMENT} commit ${RELEASE_COMMIT}."
  exit 0
fi
require_var DEPLOY_KNOWN_HOSTS_FILE; [[ -s "$DEPLOY_KNOWN_HOSTS_FILE" ]] || die 'Known-hosts file is empty.'
prepare_ssh; trap cleanup_ssh EXIT
require_var REMOTE_BENCH_ROOT; require_var FRAPPE_SITE
if [[ "$DEPLOY_ENVIRONMENT" == production ]]; then
  remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.production_config.assert_production_config_ready"
else
  remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.production_config.assert_staging_config_ready"
fi
if [[ "$DEPLOY_ENVIRONMENT" == production ]]; then
  remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.backup_readiness.assert_backup_readiness_ready"
fi
remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.migration_preflight.assert_migration_preflight_ready"
log 'Remote deployment preflight passed.'
