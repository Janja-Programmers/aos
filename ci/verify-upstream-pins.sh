#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

assert_python_version
require_command git
python_executable="$(python314)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/aos-upstream-pins.XXXXXX")"
trap 'rm -rf "${work_dir}"' EXIT

verify_tag() {
	local repository="$1"
	local tag="$2"
	local expected="$3"
	local destination="$4"
	git init -q "${destination}"
	git -C "${destination}" remote add origin "${repository}"
	git -C "${destination}" fetch -q --depth 1 origin "refs/tags/${tag}"
	git -C "${destination}" checkout -q --detach FETCH_HEAD
	local actual
	actual="$(git -C "${destination}" rev-parse 'FETCH_HEAD^{commit}')"
	[[ "${actual}" == "${expected}" ]] || die "${repository} tag ${tag} resolved to ${actual}, expected ${expected}."
}

verify_tag https://github.com/frappe/frappe.git "${FRAPPE_RELEASE}" "${FRAPPE_REF}" "${work_dir}/frappe"
"${python_executable}" - "${work_dir}/frappe" "${NODE_VERSION}" <<'PY'
import json
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
node_major = int(sys.argv[2].split(".", 1)[0])
metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
if metadata["project"]["requires-python"] != ">=3.14,<3.15":
    raise SystemExit("Pinned Frappe tag does not declare the exact Python 3.14 support range")
package = json.loads((root / "package.json").read_text(encoding="utf-8"))
if package.get("engines", {}).get("node") != ">=24" or node_major < 24:
    raise SystemExit("Pinned Frappe tag and selected Node version are incompatible")
PY

verify_tag https://github.com/frappe/bench.git "v${FRAPPE_BENCH_VERSION}" \
	"${FRAPPE_BENCH_REF}" "${work_dir}/bench"
"${python_executable}" - "${work_dir}/bench" "${FRAPPE_BENCH_VERSION}" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
expected = sys.argv[2]
init_text = (root / "bench" / "__init__.py").read_text(encoding="utf-8")
match = re.search(r'^VERSION = "([^"]+)"$', init_text, re.MULTILINE)
if not match or match.group(1) != expected:
    raise SystemExit("Bench tag version metadata does not match the selected package version")
metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
if metadata["project"]["requires-python"] != ">=3.10":
    raise SystemExit("Unexpected Bench Python metadata; compatibility requires review")
PY

printf 'Official Frappe %s/%s and Bench %s/%s metadata verification: OK\n' \
	"${FRAPPE_RELEASE}" "${FRAPPE_REF}" "${FRAPPE_BENCH_VERSION}" "${FRAPPE_BENCH_REF}"
