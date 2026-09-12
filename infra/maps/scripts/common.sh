#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MANIFEST_FILE="${MAP_MANIFEST_FILE:-${ROOT_DIR}/infra/maps/manifest.env}"

fail() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "Required command is not installed: $1"; }

validate_sha256() {
    [[ "$1" =~ ^[a-fA-F0-9]{64}$ ]] || fail "$2 must be a 64-character SHA-256 checksum."
}

validate_digest_image() {
    [[ "$1" =~ ^[^[:space:]@]+@sha256:[a-f0-9]{64}$ ]] || fail "$2 must be digest-pinned."
}

load_manifest() {
    [[ -f "${MANIFEST_FILE}" ]] || fail "Map manifest not found: ${MANIFEST_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${MANIFEST_FILE}"
    set +a
    : "${OSM_PLANET_URL:?OSM_PLANET_URL is required}"
    : "${OSM_PLANET_SHA256:?OSM_PLANET_SHA256 is required}"
    : "${OSM_PLANET_FILENAME:?OSM_PLANET_FILENAME is required}"
    : "${MAP_DATA_VERSION:?MAP_DATA_VERSION is required}"
    : "${BASEMAP_PMTILES_FILENAME:?BASEMAP_PMTILES_FILENAME is required}"
    : "${PLANETILER_IMAGE:?PLANETILER_IMAGE is required}"
    : "${PHOTON_IMAGE:?PHOTON_IMAGE is required}"
    : "${VALHALLA_IMAGE:?VALHALLA_IMAGE is required}"
    validate_sha256 "${OSM_PLANET_SHA256}" OSM_PLANET_SHA256
    validate_digest_image "${PLANETILER_IMAGE}" PLANETILER_IMAGE
    validate_digest_image "${VALHALLA_IMAGE}" VALHALLA_IMAGE
    if [[ "${PHOTON_IMAGE}" != aos-photon:* ]]; then
        validate_digest_image "${PHOTON_IMAGE}" PHOTON_IMAGE
    fi
}

ensure_image() {
    docker image inspect "$1" >/dev/null 2>&1 || docker pull "$1"
}

sha256_file() { sha256sum "$1" | awk '{print $1}'; }
assert_nonempty_file() { [[ -s "$1" ]] || fail "Expected non-empty file: $1"; }
atomic_replace() { assert_nonempty_file "$1"; mkdir -p "$(dirname "$2")"; mv -f "$1" "$2"; }
