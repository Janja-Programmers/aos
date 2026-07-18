#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

bench_path="${AOS_BENCH_PATH:-}"
site="${AOS_FRAPPE_SITE:-}"
assert_python_version
[[ -n "${bench_path}" ]] || die "Set AOS_BENCH_PATH to an existing Frappe Bench directory."
[[ -n "${site}" ]] || die "Set AOS_FRAPPE_SITE to a disposable or development test site."
[[ -d "${bench_path}/apps/frappe/.git" ]] || die "Frappe checkout missing at ${bench_path}/apps/frappe."
bench_command="$(command -v bench || true)"
[[ -n "${bench_command}" ]] || die "bench is not available. Install frappe-bench ${FRAPPE_BENCH_VERSION}."
actual_bench_version="$(${bench_command} --version)"
[[ "${actual_bench_version}" == "${FRAPPE_BENCH_VERSION}" ]] \
	|| die "Expected Bench ${FRAPPE_BENCH_VERSION}; found ${actual_bench_version}."

actual_ref="$(git -C "${bench_path}/apps/frappe" rev-parse HEAD)"
[[ "${actual_ref}" == "${FRAPPE_REF}" ]] \
    || die "Frappe revision mismatch: expected ${FRAPPE_REF}, found ${actual_ref}."

"$(python314)" "${CI_ROOT}/ci/assert_frappe_tests.py"

cd "${bench_path}"
"${bench_command}" --site "${site}" set-config allow_tests 1 --parse
if [[ "${AOS_FRAPPE_RUN_MIGRATE:-0}" == "1" ]]; then
	"${bench_command}" --site "${site}" migrate
fi

log_file="${AOS_CI_ARTIFACTS}/frappe-tests.log"
"${bench_command}" --site "${site}" run-tests --app aos 2>&1 | tee "${log_file}"
grep -Eq 'Ran [1-9][0-9]* tests?' "${log_file}" \
    || die "Frappe test output did not prove that at least one test ran."
