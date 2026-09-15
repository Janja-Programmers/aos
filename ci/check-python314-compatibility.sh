#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

target="${1:-all}"
if [[ "${target}" == "background-removal" ]]; then
    exec "${SCRIPT_DIR}/check-background-removal-compatibility.sh"
fi

assert_python_version
python_executable="$(python314)"

run_one() {
    local service="$1"
    assert_service "${service}"
	local service_dir="${CI_ROOT}/infra/${service}"
	local venv="${AOS_CI_WORKDIR}/compat-${service}"
	local report_dir="${AOS_CI_ARTIFACTS}/compatibility/${service}"
	mkdir -p "${report_dir}"

	{
		"${python_executable}" -m venv --clear "${venv}"
		"${venv}/bin/python" -m pip install --disable-pip-version-check \
			--require-hashes -r "${service_dir}/requirements.lock"
		"${venv}/bin/python" -m pip check
		"${venv}/bin/python" -m pip freeze --all >"${report_dir}/installed.txt"
		(
		cd "${service_dir}"
		PYTHONDONTWRITEBYTECODE=1 "${venv}/bin/python" "${CI_ROOT}/ci/import_service.py"
		)
	} 2>&1 | tee "${report_dir}/compatibility.log"
}

while IFS= read -r service; do
    [[ "${service}" == "background-removal" ]] && continue
    run_one "${service}"
done < <(service_list "${target}")

if [[ "${target}" == "all" ]]; then
    "${SCRIPT_DIR}/check-background-removal-compatibility.sh"
fi
