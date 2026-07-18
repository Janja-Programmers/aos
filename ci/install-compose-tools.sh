#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

[[ $# -eq 1 ]] || die "usage: install-compose-tools.sh DESTINATION"
require_command curl
destination="$1"
mkdir -p "${destination}/bin" "${destination}/downloads"

compose="${destination}/bin/docker-compose"
curl --fail --location --retry 3 --retry-all-errors \
	"https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
	-o "${compose}"
printf '%s  %s\n' "${COMPOSE_BINARY_SHA256}" "${compose}" | sha256sum -c -
chmod 0755 "${compose}"

crane_archive="${destination}/downloads/go-containerregistry_Linux_x86_64.tar.gz"
curl --fail --location --retry 3 --retry-all-errors \
	"https://github.com/google/go-containerregistry/releases/download/v${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" \
	-o "${crane_archive}"
printf '%s  %s\n' "${CRANE_ARCHIVE_SHA256}" "${crane_archive}" | sha256sum -c -
tar --no-same-owner -xzf "${crane_archive}" -C "${destination}/bin" crane
chmod 0755 "${destination}/bin/crane"

"${compose}" version | grep -F "v${COMPOSE_VERSION}" >/dev/null
"${destination}/bin/crane" version | grep -Fx "${CRANE_VERSION}" >/dev/null
printf 'Pinned Docker Compose %s and crane %s installed under %s\n' \
	"${COMPOSE_VERSION}" "${CRANE_VERSION}" "${destination}"
