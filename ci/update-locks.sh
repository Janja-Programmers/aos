#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command uv
[[ "$(uv --version | awk '{print $2}')" == "0.11.28" ]] \
	|| die "Lock regeneration requires uv 0.11.28."
python_executable="$(python314)"

for name in build quality security tests; do
	uv pip compile --python "${python_executable}" --generate-hashes \
		"${CI_ROOT}/ci/requirements/${name}.in" \
		-o "${CI_ROOT}/ci/requirements/${name}.lock"
done
uv pip compile --python "${python_executable}" --generate-hashes \
	"${CI_ROOT}/pyproject.toml" -o "${CI_ROOT}/ci/requirements/root-production.lock"
while IFS= read -r service; do
	uv pip compile --python "${python_executable}" --generate-hashes \
		"${CI_ROOT}/infra/${service}/requirements.txt" \
		-o "${CI_ROOT}/infra/${service}/requirements.lock"
	uv pip compile --python "${python_executable}" --generate-hashes \
		"${CI_ROOT}/infra/${service}/requirements-test.txt" \
		-o "${CI_ROOT}/infra/${service}/requirements-test.lock"
done <"${CI_ROOT}/ci/service-matrix.txt"
