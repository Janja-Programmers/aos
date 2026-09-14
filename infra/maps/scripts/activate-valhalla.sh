#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_valhalla_manifest

version="${1:-${MAP_DATA_VERSION}}"
[[ "${version}" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Invalid Valhalla release version: ${version}"
release_root="${ROOT_DIR}/maps/valhalla/releases"
release_dir="${release_root}/${version}"
current_link="${ROOT_DIR}/maps/valhalla/current"

[[ -d "${release_dir}" ]] || fail "Valhalla release not found: ${release_dir}"
VALHALLA_RELEASE_DIR="${release_dir}" MAP_DATA_VERSION="${version}" \
    "${SCRIPT_DIR}/verify-valhalla.sh"

mkdir -p "${release_root}"
tmp_link="${ROOT_DIR}/maps/valhalla/.current.$$"
rm -f "${tmp_link}"
ln -s "releases/${version}" "${tmp_link}"
mv -Tf "${tmp_link}" "${current_link}"

info "Activated Valhalla graph release: ${version}"
info "Recreate the runtime with: docker compose --profile maps-routing up -d --force-recreate valhalla"
