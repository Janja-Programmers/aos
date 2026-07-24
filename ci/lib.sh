#!/usr/bin/env bash
set -Eeuo pipefail

CI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${CI_ROOT}/ci/versions.env"

export AOS_CI_WORKDIR="${AOS_CI_WORKDIR:-${TMPDIR:-/tmp}/aos-ci-${UID}}"
export AOS_CI_ARTIFACTS="${AOS_CI_ARTIFACTS:-${AOS_CI_WORKDIR}/artifacts}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-${AOS_CI_WORKDIR}/pip-cache}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${AOS_CI_WORKDIR}/uv-cache}"
mkdir -p "${AOS_CI_WORKDIR}" "${AOS_CI_ARTIFACTS}" "${PIP_CACHE_DIR}" "${UV_CACHE_DIR}"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command '$1' is missing. See docs/development/testing.md."
}

python314() {
    if [[ -n "${AOS_PYTHON:-}" ]]; then
        [[ -x "${AOS_PYTHON}" ]] || die "AOS_PYTHON is not executable: ${AOS_PYTHON}"
        printf '%s\n' "${AOS_PYTHON}"
        return
    fi

    command -v "python${PYTHON_VERSION%.*}" >/dev/null 2>&1 \
        || die "Python ${PYTHON_VERSION} is required. Set AOS_PYTHON to its executable."
    command -v "python${PYTHON_VERSION%.*}"
}

assert_python_version() {
    local python_executable
    python_executable="$(python314)"
    local actual
    actual="$(${python_executable} -c 'import platform; print(platform.python_version())')"
    [[ "${actual}" == "${PYTHON_VERSION}" ]] \
        || die "Expected Python ${PYTHON_VERSION}; found ${actual} at ${python_executable}."
}

assert_service() {
    local service="$1"
    [[ -n "${service}" ]] || die "A service name is required."
    grep -Fxq "${service}" "${CI_ROOT}/ci/service-matrix.txt" \
        || die "Unknown service '${service}'. See ci/service-matrix.txt."
    [[ -f "${CI_ROOT}/infra/${service}/requirements.txt" ]] \
        || die "Production requirements missing for ${service}."
    [[ -f "${CI_ROOT}/infra/${service}/requirements.lock" ]] \
        || die "Production lock missing for ${service}."
}

service_list() {
    if [[ "${1:-all}" == "all" ]]; then
        cat "${CI_ROOT}/ci/service-matrix.txt"
    else
        assert_service "$1"
        printf '%s\n' "$1"
    fi
}
