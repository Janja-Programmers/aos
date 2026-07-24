#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
python_executable="$(python314)"

run_one() {
    local service="$1"
    assert_service "${service}"
	local service_dir="${CI_ROOT}/infra/${service}"
	local venv="${AOS_CI_WORKDIR}/unit-${service}"
	local report_dir="${AOS_CI_ARTIFACTS}/fastapi/${service}"
	local coverage_floor
	coverage_floor="$(awk -F= -v service="${service}" '$1 == service { print $2 }' "${CI_ROOT}/ci/coverage-floors.env")"
	[[ "${coverage_floor}" =~ ^[0-9]+$ ]] \
		|| die "Coverage floor missing or invalid for ${service} in ci/coverage-floors.env."
	[[ -f "${service_dir}/requirements-test.lock" ]] \
		|| die "Hash-locked test requirements missing for ${service}."

    "${python_executable}" -m venv --clear "${venv}"
    "${venv}/bin/python" -m pip install --disable-pip-version-check \
        --require-hashes -r "${CI_ROOT}/ci/requirements/tests.lock"
	"${venv}/bin/python" -m pip install --disable-pip-version-check \
		--require-hashes -r "${service_dir}/requirements-test.lock"
    "${venv}/bin/python" -m pip check
    mkdir -p "${report_dir}"

    (
        cd "${service_dir}"
        PYTHONDONTWRITEBYTECODE=1 "${venv}/bin/python" -m pytest tests \
            --strict-config \
            --strict-markers \
            --junitxml="${report_dir}/junit.xml" \
            --cov=app \
			--cov-fail-under="${coverage_floor}" \
            --cov-report="xml:${report_dir}/coverage.xml" \
            --cov-report=term-missing
    )
}

while IFS= read -r service; do
    run_one "${service}"
done < <(service_list "${1:-all}")
