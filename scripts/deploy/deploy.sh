#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
validate_environment
validate_release
require_var REMOTE_RELEASE_ROOT
require_var REMOTE_BENCH_ROOT
require_var FRAPPE_SITE
require_var REMOTE_APPLY_RELEASE_PATH
[[ "$REMOTE_RELEASE_ROOT" =~ ^/[A-Za-z0-9_./-]+$ ]] \
  || die 'REMOTE_RELEASE_ROOT must be an absolute path.'
[[ "$REMOTE_BENCH_ROOT" =~ ^/[A-Za-z0-9_./-]+$ ]] \
  || die 'REMOTE_BENCH_ROOT must be an absolute path.'
[[ "$FRAPPE_SITE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,139}$ ]] \
  || die 'FRAPPE_SITE contains unsupported characters.'
[[ "$REMOTE_APPLY_RELEASE_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]] \
  || die 'REMOTE_APPLY_RELEASE_PATH must be one reviewed absolute executable path without arguments.'

if [[ "$DRY_RUN" == "true" ]]; then
  log "Would apply immutable commit ${RELEASE_COMMIT} through ${REMOTE_APPLY_RELEASE_PATH}, then unconditionally run scripts/deploy/run-migrate.sh before smoke checks."
  exit 0
fi

require_var DEPLOY_KNOWN_HOSTS_FILE

prepare_ssh
trap cleanup_ssh EXIT
release_dir="${REMOTE_RELEASE_ROOT}/${RELEASE_COMMIT}"
remote "install -d -m 0750 '$release_dir'"
scp "${SSH_OPTS[@]}" "$RELEASE_ARTIFACT" "$RELEASE_MANIFEST" "${DEPLOY_USER}@${DEPLOY_HOST}:$release_dir/"
artifact_name="$(basename "$RELEASE_ARTIFACT")"
manifest_name="$(basename "$RELEASE_MANIFEST")"

remote "cd '$release_dir' && expected=\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding=\"utf-8\"))[\"artifact_sha256\"])' '$manifest_name') && printf '%s  %s\n' \"\$expected\" '$artifact_name' | sha256sum -c -"
remote "'$REMOTE_APPLY_RELEASE_PATH' '$release_dir/$artifact_name' '$release_dir/$manifest_name' '$RELEASE_COMMIT'"

# Repository-controlled migration is unconditional and cannot be hidden inside
# the operator apply command. A migration failure stops this script, writes the
# atomic marker in run-migrate.sh, and prevents the workflow smoke step.
remote "REMOTE_BENCH_ROOT='$REMOTE_BENCH_ROOT' FRAPPE_SITE='$FRAPPE_SITE' RELEASE_COMMIT='$RELEASE_COMMIT' '$REMOTE_BENCH_ROOT/apps/aos/scripts/deploy/run-migrate.sh'"
log "Deployment and guarded migration completed for ${RELEASE_COMMIT}."
