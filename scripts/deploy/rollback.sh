#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

DRY_RUN=false
case "${1:-}" in
  --dry-run) DRY_RUN=true ;;
  '') ;;
  *) die 'Only --dry-run is accepted.' ;;
esac

validate_environment
require_var ROLLBACK_COMMIT
[[ "$ROLLBACK_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die 'ROLLBACK_COMMIT must be the exact prior Git SHA.'
require_var ROLLBACK_ARTIFACT
require_var ROLLBACK_MANIFEST
[[ -f "$ROLLBACK_ARTIFACT" && -f "$ROLLBACK_MANIFEST" ]] || die 'Prior release archive or manifest is missing.'
[[ "$(basename "$ROLLBACK_ARTIFACT")" == aos-release.tar.gz ]] || die 'Unexpected rollback archive filename.'
[[ "$(basename "$ROLLBACK_MANIFEST")" == release-manifest.json ]] || die 'Unexpected rollback manifest filename.'
python3 scripts/deploy/release_manifest.py verify "$ROLLBACK_MANIFEST" "$ROLLBACK_ARTIFACT" "$ROLLBACK_COMMIT"

require_var VERIFIED_BACKUP_ID
[[ "$VERIFIED_BACKUP_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die 'VERIFIED_BACKUP_ID is invalid.'
require_var REMOTE_RELEASE_ROOT
require_var REMOTE_APPLY_RELEASE_PATH
require_var REMOTE_BENCH_ROOT
require_var FRAPPE_SITE

for path in "$REMOTE_RELEASE_ROOT" "$REMOTE_APPLY_RELEASE_PATH" "$REMOTE_BENCH_ROOT"; do
  [[ "$path" =~ ^/[A-Za-z0-9_./-]+$ && "$path" != *..* ]] || die 'Remote path must be a reviewed absolute path without traversal or arguments.'
done
[[ "$FRAPPE_SITE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,139}$ ]] || die 'Invalid FRAPPE_SITE.'

if [[ "$DRY_RUN" == true ]]; then
  log "Would verify and reapply immutable prior release ${ROLLBACK_COMMIT} through ${REMOTE_APPLY_RELEASE_PATH} using verified backup evidence ${VERIFIED_BACKUP_ID}; no database restore or migration is performed."
  exit 0
fi

[[ "${ROLLBACK_APPROVED:-false}" == true ]] || die 'An explicit ROLLBACK_APPROVED=true is required.'
[[ "${ROLLBACK_DB_DECISION:-}" == application-only ]] || die 'ROLLBACK_DB_DECISION=application-only is required; database restore is separate.'
require_var ROLLBACK_IMAGE_LOCK
require_var ROLLBACK_LOCKED_COMPOSE
[[ -f "$ROLLBACK_IMAGE_LOCK" && -f "$ROLLBACK_LOCKED_COMPOSE" ]] \
  || die 'Application rollback requires the exact previous promoted lock and no-build Compose.'
[[ "$(basename "$ROLLBACK_IMAGE_LOCK")" == release-image-lock.json ]] \
  || die 'Unexpected prior-release image-lock filename.'
[[ "$(basename "$ROLLBACK_LOCKED_COMPOSE")" == release-compose.locked.yml ]] \
  || die 'Unexpected prior-release locked-Compose filename.'
python3 scripts/deploy/locked_compose.py verify-registry \
  "$ROLLBACK_MANIFEST" "$ROLLBACK_ARTIFACT" "$ROLLBACK_IMAGE_LOCK" \
  "$ROLLBACK_COMMIT" "$ROLLBACK_LOCKED_COMPOSE"
require_var DEPLOY_KNOWN_HOSTS_FILE
[[ -s "$DEPLOY_KNOWN_HOSTS_FILE" ]] || die 'Verified known-hosts file is missing or empty.'

archive_hash="$(sha256sum "$ROLLBACK_ARTIFACT" | cut -d' ' -f1)"
manifest_hash="$(sha256sum "$ROLLBACK_MANIFEST" | cut -d' ' -f1)"
lock_hash="$(sha256sum "$ROLLBACK_IMAGE_LOCK" | cut -d' ' -f1)"
compose_hash="$(sha256sum "$ROLLBACK_LOCKED_COMPOSE" | cut -d' ' -f1)"
release_dir="${REMOTE_RELEASE_ROOT}/${ROLLBACK_COMMIT}"
remote_archive="$release_dir/aos-release.tar.gz"
remote_manifest="$release_dir/release-manifest.json"
remote_lock="$release_dir/release-image-lock.json"
remote_compose="$release_dir/release-compose.locked.yml"

prepare_ssh
trap cleanup_ssh EXIT

# Keep validation and application in a single remote command. No operator-supplied
# command text is accepted. An application rollback must not restore the database.
remote "test -r '$remote_archive' && test -r '$remote_manifest' \
  && printf '%s  %s\\n' '$archive_hash' '$remote_archive' | sha256sum -c - \
  && printf '%s  %s\\n' '$manifest_hash' '$remote_manifest' | sha256sum -c - \
  && printf '%s  %s\\n' '$lock_hash' '$remote_lock' | sha256sum -c - \
  && printf '%s  %s\\n' '$compose_hash' '$remote_compose' | sha256sum -c - \
  && test -x '$REMOTE_BENCH_ROOT/env/bin/python' \
  && command -v crane >/dev/null \
  && test \"\$(crane version)\" = '0.21.7' \
  && '$REMOTE_BENCH_ROOT/env/bin/python' '$release_dir/policy/locked_compose.py' \
     verify-registry '$remote_manifest' '$remote_archive' '$remote_lock' \
     '$ROLLBACK_COMMIT' '$remote_compose' \
  && '$REMOTE_APPLY_RELEASE_PATH' '$remote_archive' '$remote_manifest' \
     '$ROLLBACK_COMMIT' '$remote_lock' '$remote_compose'"

remote "cd '$REMOTE_BENCH_ROOT' \
  && bench --site '$FRAPPE_SITE' execute aos.utils.operational_health.assert_operational_health_ready \
  && bench --site '$FRAPPE_SITE' execute aos.utils.job_monitoring.assert_job_monitoring_ready"
log "Application-only rollback verified for ${ROLLBACK_COMMIT}; evidence backup ${VERIFIED_BACKUP_ID}. Database state unchanged by this wrapper."
