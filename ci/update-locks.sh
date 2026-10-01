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
security_floors="${CI_ROOT}/ci/requirements/security-floors.txt"
semgrep_floors="${CI_ROOT}/ci/requirements/semgrep-floors.txt"

# Compile ALL locks before publishing ANY of them. A solver failure cannot
# leave the repository with a mixture of new and old dependency resolutions.
stage="$(mktemp -d "${AOS_CI_WORKDIR}/locks.XXXXXXXX")"
targets=()
cleanup() {
    for relative in "${targets[@]}"; do
        rm -f -- "${CI_ROOT}/${relative}.new.$$"
    done
    rm -rf -- "${stage}"
}
trap cleanup EXIT

compile_lock() {
    local relative="$1"
    local floors="$2"
    shift 2
    mkdir -p "${stage}/$(dirname "${relative}")"
    uv pip compile "$@" --generate-hashes --constraint "${floors}" \
        --custom-compile-command "ci/update-locks.sh (uv 0.11.28; ${relative})" \
        -o "${stage}/${relative}"
    targets+=("${relative}")
}

for name in build quality security semgrep tests; do
    floors="${security_floors}"
    additional_options=()
    if [[ "${name}" == semgrep ]]; then
        floors="${semgrep_floors}"
        # Explicit exception to Semgrep's upstream PyJWT metadata ceiling;
        # maintain the full hash-locked dependency inventory and audit it.
        additional_options=(--override "${CI_ROOT}/ci/requirements/semgrep-overrides.txt")
    fi
    compile_lock "ci/requirements/${name}.lock" "${floors}" \
        "${additional_options[@]}" --python "${python_executable}" \
        "${CI_ROOT}/ci/requirements/${name}.in"
done
compile_lock "ci/requirements/root-production.lock" "${security_floors}" \
    --python "${python_executable}" "${CI_ROOT}/pyproject.toml"
while IFS= read -r service; do
    if [[ "${service}" == "background-removal" ]]; then
        runtime=(--python-version "${BACKGROUND_REMOVAL_PYTHON_VERSION}")
    else
        runtime=(--python "${python_executable}")
    fi
    compile_lock "infra/${service}/requirements.lock" "${security_floors}" \
        "${runtime[@]}" "${CI_ROOT}/infra/${service}/requirements.txt"
    compile_lock "infra/${service}/requirements-test.lock" "${security_floors}" \
        "${runtime[@]}" "${CI_ROOT}/infra/${service}/requirements-test.txt"
done <"${CI_ROOT}/ci/service-matrix.txt"

# Stage all replacement files before touching tracked locks. Each individual
# rename is atomic; resolution failure above leaves every prior lock intact.
for relative in "${targets[@]}"; do
    cp -- "${stage}/${relative}" "${CI_ROOT}/${relative}.new.$$"
done
for relative in "${targets[@]}"; do
    mv -f -- "${CI_ROOT}/${relative}.new.$$" "${CI_ROOT}/${relative}"
done
printf 'Regenerated %s hashed dependency locks.\n' "${#targets[@]}"
