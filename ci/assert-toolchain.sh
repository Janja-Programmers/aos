#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
if command -v node >/dev/null 2>&1; then
	actual_node="$(node --version)"
	[[ "${actual_node}" == "v${NODE_VERSION}" ]] \
		|| die "Expected Node v${NODE_VERSION}; found ${actual_node}."
fi
printf 'Python %s and version manifest: OK\n' "${PYTHON_VERSION}"
