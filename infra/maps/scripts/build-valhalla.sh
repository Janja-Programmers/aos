#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_valhalla_manifest
require_command docker
require_command sha256sum
require_command python3

source_file="${ROOT_DIR}/maps/downloads/${OSM_PLANET_FILENAME}"
assert_nonempty_file "${source_file}"
actual_planet_sha="$(sha256_file "${source_file}")"
[[ "${actual_planet_sha,,}" == "${OSM_PLANET_SHA256,,}" ]] || \
    fail "Planet SHA-256 mismatch before Valhalla build: ${actual_planet_sha}"

ensure_image "${VALHALLA_IMAGE}"

release_root="${ROOT_DIR}/maps/valhalla/releases"
release_dir="${release_root}/${MAP_DATA_VERSION}"
staging_dir="${release_root}/.${MAP_DATA_VERSION}.building.$$"

if [[ -e "${release_dir}" ]]; then
    fail "Valhalla release already exists and is immutable: ${release_dir}"
fi

rm -rf "${staging_dir}"
mkdir -p "${staging_dir}"
trap 'rm -rf "${staging_dir}"' EXIT

info "Building global Valhalla graph for ${MAP_DATA_VERSION} outside request-serving containers"
docker run --rm --security-opt no-new-privileges:true \
    -e tile_urls="" \
    -e use_tiles_ignore_pbf=False \
    -e force_rebuild=True \
    -e build_admins=True \
    -e build_time_zones=True \
    -e build_elevation=False \
    -e build_transit=False \
    -e build_tar=True \
    -e serve_tiles=False \
    -e update_existing_config=True \
    -e use_default_speeds_config=True \
    -e server_threads="${VALHALLA_BUILD_THREADS:-8}" \
    -e tileset_name="${VALHALLA_TILESET_NAME:-aos_world_valhalla}" \
    -v "${staging_dir}:/custom_files:rw" \
    -v "${source_file}:/custom_files/planet.osm.pbf:ro" \
    "${VALHALLA_IMAGE}"

MAP_DATA_VERSION="${MAP_DATA_VERSION}" \
VALHALLA_RELEASE_DIR="${staging_dir}" \
"${SCRIPT_DIR}/verify-valhalla.sh" --allow-missing-release-manifest

python3 - "${staging_dir}" "${MAP_DATA_VERSION}" "${OSM_PLANET_FILENAME}" \
    "${OSM_PLANET_SHA256,,}" "${VALHALLA_IMAGE}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

release = Path(sys.argv[1])
version = sys.argv[2]
planet_filename = sys.argv[3]
planet_sha256 = sys.argv[4]
image = sys.argv[5]

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

config_path = release / "valhalla.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
mjolnir = config.get("mjolnir") or {}

def local_path(value: str) -> Path:
    prefix = "/custom_files/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SystemExit(f"Unexpected Valhalla artifact path: {value!r}")
    return release / value[len(prefix):]

artifacts = {}
for name, key in (
    ("tile_extract", "tile_extract"),
    ("admin", "admin"),
    ("timezone", "timezone"),
):
    path = local_path(mjolnir.get(key, ""))
    artifacts[name] = {
        "path": str(path.relative_to(release)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }

manifest = {
    "schema": 1,
    "map_data_version": version,
    "planet": {
        "filename": planet_filename,
        "sha256": planet_sha256,
    },
    "valhalla_image": image,
    "artifacts": artifacts,
}
(release / "aos-valhalla-manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
PY

VALHALLA_RELEASE_DIR="${staging_dir}" "${SCRIPT_DIR}/verify-valhalla.sh"

mv "${staging_dir}" "${release_dir}"
trap - EXIT
info "Valhalla release built and verified: ${release_dir}"
info "Activate it with: ${SCRIPT_DIR}/activate-valhalla.sh ${MAP_DATA_VERSION}"
