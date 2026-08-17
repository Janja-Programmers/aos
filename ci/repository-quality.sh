#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command git
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/quality"

git -C "${CI_ROOT}" diff --exit-code
"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes -r "${CI_ROOT}/ci/requirements/quality.lock"

PYTHONDONTWRITEBYTECODE=1 "${python_executable}" -m compileall -q "${CI_ROOT}/aos" "${CI_ROOT}/infra"
quality_paths=("${CI_ROOT}/ci")
while IFS= read -r service; do
	quality_paths+=("${CI_ROOT}/infra/${service}/tests")
done <"${CI_ROOT}/ci/service-matrix.txt"
"${venv}/bin/ruff" check "${quality_paths[@]}"
"${venv}/bin/ruff" format --check "${quality_paths[@]}"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_repository.py" "${CI_ROOT}"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_actions.py"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_foundation.py"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_rate_limit_coverage.py" "${CI_ROOT}"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_api_documentation.py"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_doc_paths.py"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_monitoring.py" "${CI_ROOT}"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_deployment.py" "${CI_ROOT}"

(
	cd "${CI_ROOT}"
	PATH="${venv}/bin:${PATH}" PRE_COMMIT_HOME="${AOS_CI_WORKDIR}/pre-commit" \
		"${venv}/bin/pre-commit" run --all-files
)

if ! git -C "${CI_ROOT}" diff --exit-code; then
    printf 'Repository checks modified tracked files; refusing to continue.\n' >&2
    exit 1
fi
