#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command git
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/security"
report="${AOS_CI_ARTIFACTS}/detect-secrets.json"

"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes -r "${CI_ROOT}/ci/requirements/security.lock"

"${venv}/bin/python" "${CI_ROOT}/ci/repository_hygiene.py"
"${venv}/bin/python" "${CI_ROOT}/ci/validate_lock_credentials.py"
(
	cd "${CI_ROOT}"
	# Git-tracked plus non-ignored untracked source; never recurse into downloaded
	# model weights, map datasets, caches, or local virtual environments.
	mapfile -d '' -t candidate_files < <(
		git ls-files --cached --others --exclude-standard -z --
	)
	source_files=()
	for source_file in "${candidate_files[@]}"; do
		if [[ -f "${source_file}" && ! -L "${source_file}" ]]; then
			source_files+=("${source_file}")
		fi
	done
	if (( ${#source_files[@]} == 0 )); then
		printf 'Secret scan source inventory is empty; aborting.\n' >&2
		exit 1
	fi
	"${venv}/bin/detect-secrets" scan --all-files \
		--exclude-files '(^|/)(\.git|\.venv|node_modules|__pycache__|\.pytest_cache|\.ruff_cache|coverage)(/|$)|(^|/)\.secrets\.baseline$' \
		"${source_files[@]}" \
		>"${report}"
)

"${venv}/bin/python" "${CI_ROOT}/ci/compare_secret_baseline.py" \
	"${CI_ROOT}/.secrets.baseline" "${report}"
