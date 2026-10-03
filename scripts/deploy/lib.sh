#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }
require_var() { [[ -n "${!1:-}" ]] || die "Required variable is missing: $1"; }
is_placeholder() { local v="${1,,}"; [[ "$v" == *replace* || "$v" == *example.* || "$v" == *invalid* || "$v" == *changeme* ]]; }
validate_environment() { [[ "${DEPLOY_ENVIRONMENT:-}" == staging || "${DEPLOY_ENVIRONMENT:-}" == production ]] || die 'DEPLOY_ENVIRONMENT must be staging or production.'; }
validate_release() {
  require_var RELEASE_COMMIT; require_var RELEASE_ARTIFACT; require_var RELEASE_MANIFEST;
  [[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die 'RELEASE_COMMIT must be a full immutable Git SHA.'
  [[ -f "$RELEASE_ARTIFACT" && -f "$RELEASE_MANIFEST" ]] || die 'Release artifact or manifest is missing.'
  python3 scripts/deploy/release_manifest.py verify "$RELEASE_MANIFEST" "$RELEASE_ARTIFACT" "$RELEASE_COMMIT"
}
prepare_ssh() {
  require_var DEPLOY_HOST; require_var DEPLOY_USER; require_var DEPLOY_SSH_PRIVATE_KEY
  is_placeholder "$DEPLOY_HOST" && die 'DEPLOY_HOST is a placeholder.'
  SSH_KEY_FILE="$(mktemp)"; chmod 0600 "$SSH_KEY_FILE"; printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" >"$SSH_KEY_FILE"
  SSH_OPTS=(-i "$SSH_KEY_FILE" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="${DEPLOY_KNOWN_HOSTS_FILE}")
}
cleanup_ssh() { [[ -n "${SSH_KEY_FILE:-}" ]] && rm -f "$SSH_KEY_FILE"; }
remote() { ssh "${SSH_OPTS[@]}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"; }

# Carry one host-wide nonblocking transaction lock across activation, guarded
# migration/restart and application-only rollback. SSH delivers the complete
# script on stdin: no interpolated shell command or release input is evaluated
# outside the lock. The inner applier retains its separate activation lock.
remote_transaction() {
  require_var REMOTE_RELEASE_ROOT
  local root="${REMOTE_RELEASE_ROOT%/}"
  [[ "$root" =~ ^/[A-Za-z0-9_./-]+$ && "$root" != "/" && "$root" != *..* ]] \
    || die 'Transaction root must be a reviewed absolute release directory.'
  local lock="$root/.deployment-transaction.lock"
  remote "umask 077 && test -d '$root' && test ! -L '$root' && test ! -L '$lock' && flock -n -E 75 '$lock' bash -Eeuo pipefail -s" <<< "$1"
}
