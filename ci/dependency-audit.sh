#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/audit"
report_dir="${AOS_CI_ARTIFACTS}/pip-audit"
mkdir -p "${report_dir}"

"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes -r "${CI_ROOT}/ci/requirements/security.lock"

inputs=(
	"ci/requirements/build.lock"
	"ci/requirements/root-production.lock"
    "ci/requirements/quality.lock"
    "ci/requirements/security.lock"
    "ci/requirements/tests.lock"
)
while IFS= read -r service; do
	inputs+=("infra/${service}/requirements.lock")
	inputs+=("infra/${service}/requirements-test.lock")
done <"${CI_ROOT}/ci/service-matrix.txt"

status=0
for relative in "${inputs[@]}"; do
    input="${CI_ROOT}/${relative}"
    [[ -f "${input}" ]] || die "Audit input is missing: ${relative}"
    report_name="${relative//\//_}.json"
    if ! "${venv}/bin/pip-audit" --disable-pip --progress-spinner off \
        --format json --output "${report_dir}/${report_name}" --requirement "${input}"; then
        printf 'Vulnerability audit failed for %s. See its JSON artifact.\n' "${relative}" >&2
        status=1
    fi
done
exit "${status}"
