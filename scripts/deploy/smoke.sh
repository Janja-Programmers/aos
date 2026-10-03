#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
DRY_RUN=false; [[ "${1:-}" == --dry-run ]] && DRY_RUN=true
validate_environment
if [[ "$DRY_RUN" == true ]]; then log "Would run health and job-monitoring smoke checks for ${DEPLOY_ENVIRONMENT}."; exit 0; fi
require_var DEPLOY_KNOWN_HOSTS_FILE; require_var REMOTE_BENCH_ROOT; require_var REMOTE_RELEASE_ROOT; require_var FRAPPE_SITE; require_var RELEASE_COMMIT
[[ "$REMOTE_BENCH_ROOT" =~ ^/[A-Za-z0-9_./-]+$ && "$REMOTE_BENCH_ROOT" != *..* ]] || die 'Invalid Bench root.'
[[ "$FRAPPE_SITE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,139}$ ]] || die 'Invalid FRAPPE_SITE.'
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die 'Smoke checks require the exact deployed Git commit.'
prepare_ssh; trap cleanup_ssh EXIT
# Never report a different release as healthy if an authorized rollback or a
# newer deployment won the lock between migration and this workflow step.
remote_transaction "test \"\$(readlink -f '$REMOTE_BENCH_ROOT/apps/aos')\" = '$REMOTE_RELEASE_ROOT/$RELEASE_COMMIT/source' \
  && cd '$REMOTE_BENCH_ROOT' \
  && bench --site '$FRAPPE_SITE' execute aos.utils.operational_health.assert_operational_health_ready \
  && bench --site '$FRAPPE_SITE' execute aos.utils.job_monitoring.assert_job_monitoring_ready \
  && bench --site '$FRAPPE_SITE' execute aos.tasks.outbox.publish_transactional_outbox"
log 'Post-deployment smoke checks passed.'
