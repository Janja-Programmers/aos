#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command git
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/semgrep"
rules_root="$(mktemp -d "${AOS_CI_WORKDIR}/semgrep-rules.XXXXXX")"
frappe_rules="${rules_root}/frappe"
community_rules="${rules_root}/community"
report="${AOS_CI_ARTIFACTS}/semgrep.json"

"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes -r "${CI_ROOT}/ci/requirements/security.lock"

"${CI_ROOT}/ci/fetch-rules.sh" "${frappe_rules}" \
    https://github.com/frappe/semgrep-rules.git "${FRAPPE_SEMGREP_RULES_REF}"
"${CI_ROOT}/ci/fetch-rules.sh" "${community_rules}" \
    https://github.com/semgrep/semgrep-rules.git "${SEMGREP_COMMUNITY_RULES_REF}"

export SEMGREP_SEND_METRICS=off
export SEMGREP_ENABLE_VERSION_CHECK=0
cd "${CI_ROOT}"
"${venv}/bin/semgrep" scan \
    --metrics=off \
    --error \
    --severity ERROR \
    --json \
    --json-output "${report}" \
    --config "${frappe_rules}/rules" \
    --config "${community_rules}/python/lang/correctness" \
    --config "${community_rules}/python/lang/security" \
    --config "${community_rules}/python/fastapi/security" \
    --config "${community_rules}/python/requests/security" \
    aos infra
