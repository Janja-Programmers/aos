#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 2 ]] || {
	printf 'usage: verify-image-manifests.sh RENDERED_COMPOSE EVIDENCE_FILE\n' >&2
	exit 2
}

rendered="$1"
evidence="$2"
command -v crane >/dev/null 2>&1 || {
	printf 'Pinned crane is required to verify remote manifests.\n' >&2
	exit 1
}
mkdir -p "$(dirname "${evidence}")"
: >"${evidence}"

while IFS= read -r image; do
	[[ "${image}" == *@sha256:* ]] || {
		printf 'Non-digest image reached manifest verifier: %s\n' "${image}" >&2
		exit 1
	}
	expected="${image##*@sha256:}"
	actual="$(crane digest "${image}")"
	actual="${actual#sha256:}"
	[[ "${actual}" == "${expected}" ]] || {
		printf 'Registry manifest digest mismatch for %s (expected %s, received %s).\n' \
			"${image%%@*}" "${expected}" "${actual}" >&2
		exit 1
	}
	printf '%s verified-sha256=%s\n' "${image%%@*}" "${actual}" >>"${evidence}"
done < <(python - "${rendered}" <<'PY'
import sys
from pathlib import Path

import yaml

data = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("\n".join(sorted({service["image"] for service in data["services"].values() if service.get("image")})))
PY
)

[[ -s "${evidence}" ]] || {
	printf 'No external Compose images were available for manifest verification.\n' >&2
	exit 1
}
printf 'External image manifest verification: OK\n'
