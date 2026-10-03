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

require_var RELEASE_IMAGE_LOCK
require_var RELEASE_LOCKED_COMPOSE
[[ -f "$RELEASE_IMAGE_LOCK" && -f "$RELEASE_LOCKED_COMPOSE" ]] \
  || die 'A complete approved promoted image lock and no-build Compose are required.'
[[ "$(basename "$RELEASE_IMAGE_LOCK")" == release-image-lock.json ]] \
  || die 'Unexpected image-lock filename.'
[[ "$(basename "$RELEASE_LOCKED_COMPOSE")" == release-compose.locked.yml ]] \
  || die 'Unexpected locked-Compose filename.'
python3 scripts/deploy/locked_compose.py verify-registry \
  "$RELEASE_MANIFEST" "$RELEASE_ARTIFACT" "$RELEASE_IMAGE_LOCK" \
  "$RELEASE_COMMIT" "$RELEASE_LOCKED_COMPOSE"

require_var DEPLOY_KNOWN_HOSTS_FILE
[[ -s "$DEPLOY_KNOWN_HOSTS_FILE" ]] || die 'Verified known-hosts file is missing.'
[[ "$REMOTE_RELEASE_ROOT" != *..* && "$REMOTE_BENCH_ROOT" != *..* && "$REMOTE_APPLY_RELEASE_PATH" != *..* ]] \
  || die 'Remote paths must not contain traversal.'

prepare_ssh
trap cleanup_ssh EXIT

release_dir="${REMOTE_RELEASE_ROOT}/${RELEASE_COMMIT}"
remote_archive="$release_dir/aos-release.tar.gz"
remote_manifest="$release_dir/release-manifest.json"
remote_lock="$release_dir/release-image-lock.json"
remote_compose="$release_dir/release-compose.locked.yml"

remote "install -d -m 0750 '$release_dir' '$release_dir/policy'"
scp "${SSH_OPTS[@]}" "$RELEASE_ARTIFACT" "$RELEASE_MANIFEST" \
  "$RELEASE_IMAGE_LOCK" "$RELEASE_LOCKED_COMPOSE" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:$release_dir/"
scp "${SSH_OPTS[@]}" scripts/deploy/release_manifest.py \
  scripts/deploy/image_lock.py scripts/deploy/locked_compose.py \
  "${DEPLOY_USER}@${DEPLOY_HOST}:$release_dir/policy/"

for file in "$RELEASE_ARTIFACT" "$RELEASE_MANIFEST" "$RELEASE_IMAGE_LOCK" "$RELEASE_LOCKED_COMPOSE"; do
  hash="$(sha256sum "$file" | cut -d' ' -f1)"
  case "$(basename "$file")" in
    aos-release.tar.gz) target="$remote_archive" ;;
    release-manifest.json) target="$remote_manifest" ;;
    release-image-lock.json) target="$remote_lock" ;;
    release-compose.locked.yml) target="$remote_compose" ;;
    *) die 'Unexpected release source file.' ;;
  esac
  remote "printf '%s  %s\\n' '$hash' '$target' | sha256sum -c -"
done

# The remote host independently reopens and verifies the exact release archive,
# promoted OCI lock and the no-build Compose before invoking its reviewed applier.
# Bench's pinned Python has PyYAML; crane must be installed at the reviewed version.
remote "test -x '$REMOTE_BENCH_ROOT/env/bin/python' \
  && command -v crane >/dev/null \
  && test '\$(crane version)' = '0.21.7' \
  && '$REMOTE_BENCH_ROOT/env/bin/python' '$release_dir/policy/locked_compose.py' \
     verify-registry '$remote_manifest' '$remote_archive' '$remote_lock' \
     '$RELEASE_COMMIT' '$remote_compose'"

# The externally installed applier MUST implement the reviewed five-argument
# contract and consume the lock and rendered Compose. A three-argument applier
# must fail rather than silently rebuild or use floating image tags.
remote "'$REMOTE_APPLY_RELEASE_PATH' '$remote_archive' '$remote_manifest' \
  '$RELEASE_COMMIT' '$remote_lock' '$remote_compose'"

# Repository-controlled migration is unconditional. A failure stops here and
# prevents the workflow's smoke and production steps.
remote "REMOTE_BENCH_ROOT='$REMOTE_BENCH_ROOT' FRAPPE_SITE='$FRAPPE_SITE' \
  RELEASE_COMMIT='$RELEASE_COMMIT' \
  '$REMOTE_BENCH_ROOT/apps/aos/scripts/deploy/run-migrate.sh'"
log "Locked deployment and guarded migration completed for ${RELEASE_COMMIT}."
