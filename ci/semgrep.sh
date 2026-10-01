#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command git
python_executable="$(python314)"
venv="${AOS_CI_WORKDIR}/semgrep"
rules_root="$(mktemp -d "${AOS_CI_WORKDIR}/semgrep-rules.XXXXXX")"
frappe_rules="${rules_root}/frappe"
community_rules="${rules_root}/community"
report="${AOS_CI_ARTIFACTS}/semgrep.json"

"${python_executable}" -m venv --clear "${venv}"
# The upstream Semgrep wheel still declares PyJWT <2.14. Its CI-only
# override is resolved in semgrep.lock; install EVERY package and transitive
# dependency from the complete hashed inventory without re-resolving metadata.
# Never use --no-deps for AOS production or other CI toolchains.
"${venv}/bin/python" -m pip install --disable-pip-version-check \
    --no-deps --require-hashes -r "${CI_ROOT}/ci/requirements/semgrep.lock"

"${venv}/bin/python" - <<'PYVERIFY'
from importlib.metadata import version
import jwt

if version("semgrep") != "1.178.0" or version("PyJWT") != "2.15.1":
    raise SystemExit("Semgrep/PyJWT version guard failed")
token = jwt.encode({"iss": "aos-ci-smoke"}, "local-smoke-key", algorithm="HS256")
if jwt.decode(token, "local-smoke-key", algorithms=["HS256"])["iss"] != "aos-ci-smoke":
    raise SystemExit("PyJWT encode/decode compatibility smoke failed")
PYVERIFY

# Report and tightly scope the sole expected upstream metadata mismatch.
# Any other broken dependency remains a hard CI failure.
if ! check_report="$("${venv}/bin/python" -m pip check 2>&1)"; then
    CHECK_REPORT="${check_report}" "${venv}/bin/python" - <<'PYCHECK'
import os

problems = [line.strip() for line in os.environ["CHECK_REPORT"].splitlines() if line.strip()]
if (
    len(problems) != 1
    or not problems[0].lower().startswith("semgrep 1.178.0 has requirement pyjwt")
    or not problems[0].lower().endswith("but you have pyjwt 2.15.1.")
):
    raise SystemExit("Unexpected scanner dependency conflict: " + "; ".join(problems))
print("Only the explicitly documented Semgrep/PyJWT metadata ceiling differs.")
PYCHECK
fi

# The ruleset engine must execute, return valid JSON, and find a seeded issue.
# A successful dependency resolution alone is not enough.
smoke_dir="$(mktemp -d "${AOS_CI_WORKDIR}/semgrep-smoke.XXXXXXXX")"
cat >"${smoke_dir}/smoke.yml" <<'YAML'
rules:
  - id: aos-semgrep-override-smoke
    message: Seeded smoke finding
    languages: [python]
    severity: ERROR
    pattern: eval(...)
YAML
printf 'eval("1 + 1")\n' >"${smoke_dir}/seed.py"
export SEMGREP_SEND_METRICS=off
export SEMGREP_ENABLE_VERSION_CHECK=0
"${venv}/bin/semgrep" scan --metrics=off \
    --config "${smoke_dir}/smoke.yml" --json "${smoke_dir}/seed.py" \
    >"${smoke_dir}/result.json"
"${venv}/bin/python" - "${smoke_dir}/result.json" <<'PYSMOKE'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("errors") or not any(
    finding.get("check_id", "").endswith("aos-semgrep-override-smoke")
    for finding in report.get("results", [])
):
    raise SystemExit("Semgrep scanner compatibility smoke failed")
print("Semgrep scanner compatibility smoke: OK")
PYSMOKE

"${CI_ROOT}/ci/fetch-rules.sh" "${frappe_rules}" \
    https://github.com/frappe/semgrep-rules.git "${FRAPPE_SEMGREP_RULES_REF}"
"${CI_ROOT}/ci/fetch-rules.sh" "${community_rules}" \
    https://github.com/semgrep/semgrep-rules.git "${SEMGREP_COMMUNITY_RULES_REF}"

export SEMGREP_SEND_METRICS=off
export SEMGREP_ENABLE_VERSION_CHECK=0
cd "${CI_ROOT}"
"${venv}/bin/semgrep" scan \
    --metrics=off \
    --error \
    --severity ERROR \
    --json \
    --json-output "${report}" \
    --config "${frappe_rules}/rules" \
    --config "${community_rules}/python/lang/correctness" \
    --config "${community_rules}/python/lang/security" \
    --config "${community_rules}/python/fastapi/security" \
    --config "${community_rules}/python/requests/security" \
    aos infra
