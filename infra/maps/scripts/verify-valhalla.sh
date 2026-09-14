#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

allow_missing_manifest=false
if [[ "${1:-}" == "--allow-missing-release-manifest" ]]; then
    allow_missing_manifest=true
    shift
fi
[[ "$#" -eq 0 ]] || fail "usage: verify-valhalla.sh [--allow-missing-release-manifest]"

load_valhalla_manifest
require_command python3
require_command sha256sum

release_dir="${VALHALLA_RELEASE_DIR:-${ROOT_DIR}/maps/valhalla/releases/${MAP_DATA_VERSION}}"
[[ -d "${release_dir}" ]] || fail "Valhalla release directory is missing: ${release_dir}"

python3 - "${release_dir}" "${MAP_DATA_VERSION}" "${OSM_PLANET_SHA256,,}" \
    "${VALHALLA_IMAGE}" "${allow_missing_manifest}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

release = Path(sys.argv[1]).resolve()
version = sys.argv[2]
planet_sha = sys.argv[3]
image = sys.argv[4]
allow_missing_manifest = sys.argv[5].lower() == "true"

config_path = release / "valhalla.json"
if not config_path.is_file() or config_path.stat().st_size == 0:
    raise SystemExit(f"Missing Valhalla config: {config_path}")

try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    raise SystemExit(f"Invalid Valhalla config JSON: {exc}") from exc

mjolnir = config.get("mjolnir")
if not isinstance(mjolnir, dict):
    raise SystemExit("Valhalla config is missing mjolnir settings")

prefix = "/custom_files/"
def resolve_config_path(key: str) -> Path:
    value = mjolnir.get(key)
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SystemExit(f"mjolnir.{key} must point inside /custom_files")
    path = (release / value[len(prefix):]).resolve()
    try:
        path.relative_to(release)
    except ValueError as exc:
        raise SystemExit(f"mjolnir.{key} escapes the release directory") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"Missing/non-empty Valhalla artifact for mjolnir.{key}: {path}")
    return path

paths = {
    "tile_extract": resolve_config_path("tile_extract"),
    "admin": resolve_config_path("admin"),
    "timezone": resolve_config_path("timezone"),
}

# Runtime must consume the indexed tar, not rely on an unpacked mutable graph.
if paths["tile_extract"].suffix != ".tar":
    raise SystemExit("Valhalla tile_extract must be a .tar graph artifact")

manifest_path = release / "aos-valhalla-manifest.json"
if not manifest_path.exists():
    if allow_missing_manifest:
        print("Valhalla graph/config/admin/timezone artifacts: OK")
        raise SystemExit(0)
    raise SystemExit(f"Missing release manifest: {manifest_path}")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schema") != 1:
    raise SystemExit("Unsupported Valhalla release manifest schema")
if manifest.get("map_data_version") != version:
    raise SystemExit("Valhalla release manifest version does not match MAP_DATA_VERSION")
if (manifest.get("planet") or {}).get("sha256", "").lower() != planet_sha:
    raise SystemExit("Valhalla release manifest planet SHA-256 does not match the map manifest")
if manifest.get("valhalla_image") != image:
    raise SystemExit("Valhalla release manifest image does not match the map manifest")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

artifacts = manifest.get("artifacts") or {}
for name, path in paths.items():
    item = artifacts.get(name)
    if not isinstance(item, dict):
        raise SystemExit(f"Release manifest is missing artifact: {name}")
    if item.get("path") != str(path.relative_to(release)):
        raise SystemExit(f"Release manifest path mismatch for {name}")
    if int(item.get("bytes", -1)) != path.stat().st_size:
        raise SystemExit(f"Release manifest size mismatch for {name}")
    if str(item.get("sha256", "")).lower() != sha256(path):
        raise SystemExit(f"Release manifest SHA-256 mismatch for {name}")

print("Valhalla release manifest and graph artifacts: OK")
PY
