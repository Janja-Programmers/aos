#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
DRY_RUN=false; [[ "${1:-}" == --dry-run ]] && DRY_RUN=true
validate_environment
if [[ "$DRY_RUN" == true ]]; then log "Would run health and job-monitoring smoke checks for ${DEPLOY_ENVIRONMENT}."; exit 0; fi
require_var DEPLOY_KNOWN_HOSTS_FILE; require_var REMOTE_BENCH_ROOT; require_var FRAPPE_SITE
prepare_ssh; trap cleanup_ssh EXIT
remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.operational_health.assert_operational_health_ready"
remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.utils.job_monitoring.assert_job_monitoring_ready"
remote "cd '$REMOTE_BENCH_ROOT' && bench --site '$FRAPPE_SITE' execute aos.tasks.outbox.publish_transactional_outbox"
log 'Post-deployment smoke checks passed.'
