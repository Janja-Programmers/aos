#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="${ROOT_DIR}/infra/maps/scripts/common.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

cat > "${TMP_DIR}/photon.env" <<'MANIFEST'
PHOTON_IMAGE=aos-photon:1.2.0
PHOTON_DUMP_LOCAL_PATH=/tmp/example.jsonl.zst
PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES=opensearch:9200
MANIFEST

MAP_MANIFEST_FILE="${TMP_DIR}/photon.env" bash -c '
    source "$1"
    load_photon_manifest
    [[ "$PHOTON_IMAGE" == "aos-photon:1.2.0" ]]
' _ "${COMMON}"

if MAP_MANIFEST_FILE="${TMP_DIR}/photon.env" bash -c '
    source "$1"
    load_basemap_manifest
' _ "${COMMON}" >/dev/null 2>&1; then
    echo "ERROR: basemap manifest unexpectedly accepted Photon-only configuration" >&2
    exit 1
fi

cat > "${TMP_DIR}/basemap.env" <<'MANIFEST'
OSM_PLANET_URL=https://example.invalid/planet.osm.pbf
OSM_PLANET_SHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
OSM_PLANET_FILENAME=planet.osm.pbf
MAP_DATA_VERSION=20260912
BASEMAP_PMTILES_FILENAME=aos-world.pmtiles
PLANETILER_IMAGE=ghcr.io/example/planetiler@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
MANIFEST

MAP_MANIFEST_FILE="${TMP_DIR}/basemap.env" bash -c '
    source "$1"
    load_basemap_manifest
    [[ "$MAP_DATA_VERSION" == "20260912" ]]
' _ "${COMMON}"

if MAP_MANIFEST_FILE="${TMP_DIR}/basemap.env" bash -c '
    source "$1"
    load_photon_manifest
' _ "${COMMON}" >/dev/null 2>&1; then
    echo "ERROR: Photon manifest unexpectedly accepted basemap-only configuration" >&2
    exit 1
fi


cat > "${TMP_DIR}/valhalla.env" <<'MANIFEST'
OSM_PLANET_URL=https://example.invalid/planet.osm.pbf
OSM_PLANET_SHA256=cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
OSM_PLANET_FILENAME=planet.osm.pbf
MAP_DATA_VERSION=20260914
VALHALLA_IMAGE=ghcr.io/example/valhalla@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
MANIFEST

MAP_MANIFEST_FILE="${TMP_DIR}/valhalla.env" bash -c '
    source "$1"
    load_valhalla_manifest
    [[ "$MAP_DATA_VERSION" == "20260914" ]]
' _ "${COMMON}"

cat > "${TMP_DIR}/valhalla-without-version.env" <<'MANIFEST'
OSM_PLANET_URL=https://example.invalid/planet.osm.pbf
OSM_PLANET_SHA256=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
OSM_PLANET_FILENAME=planet.osm.pbf
VALHALLA_IMAGE=ghcr.io/example/valhalla@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
MANIFEST

if MAP_MANIFEST_FILE="${TMP_DIR}/valhalla-without-version.env" bash -c '
    source "$1"
    load_valhalla_manifest
' _ "${COMMON}" >/dev/null 2>&1; then
    echo "ERROR: Valhalla manifest unexpectedly accepted configuration without MAP_DATA_VERSION" >&2
    exit 1
fi

echo "Maps manifest concern scoping: OK"
