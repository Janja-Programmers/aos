#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/production-foundation"
artifacts="${AOS_CI_ARTIFACTS}/production-foundation"
mkdir -p "${artifacts}"
"${python_executable}" -m venv --clear "${venv}"
"${venv}/bin/python" -m pip install --disable-pip-version-check \
  --require-hashes -r "${CI_ROOT}/ci/requirements/tests.lock"
cd "${CI_ROOT}"
PYTHONDONTWRITEBYTECODE=1 "${venv}/bin/python" -m pytest -q infra/backup/tests \
  --junitxml="${artifacts}/backup-tests.xml" \
  2>&1 | tee "${artifacts}/backup-tests.log"
