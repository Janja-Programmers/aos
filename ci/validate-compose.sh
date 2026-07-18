#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
if [[ -n "${AOS_COMPOSE_BIN:-}" ]]; then
	[[ -x "${AOS_COMPOSE_BIN}" ]] || die "AOS_COMPOSE_BIN is not executable: ${AOS_COMPOSE_BIN}"
	compose=("${AOS_COMPOSE_BIN}")
elif command -v docker-compose >/dev/null 2>&1; then
	compose=(docker-compose)
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
	compose=(docker compose)
else
	die "Pinned Docker Compose is required. Run ci/install-compose-tools.sh and set AOS_COMPOSE_BIN."
fi
"${compose[@]}" version >/dev/null

python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/compose"
report_dir="${AOS_CI_ARTIFACTS}/compose"
mkdir -p "${report_dir}"
rendered="${report_dir}/rendered-compose.yml"
"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
	--require-hashes -r "${CI_ROOT}/ci/requirements/quality.lock"

cd "${CI_ROOT}"
"${venv}/bin/python" ci/validate_repository.py "${CI_ROOT}" --yaml-only
"${compose[@]}" --env-file ci/compose.env config --quiet
"${compose[@]}" --env-file ci/compose.env config >"${rendered}"
"${venv}/bin/python" ci/compose_policy.py "${rendered}"
PATH="${venv}/bin:${PATH}" ci/verify-image-manifests.sh \
	"${rendered}" "${report_dir}/image-manifest-evidence.txt"
