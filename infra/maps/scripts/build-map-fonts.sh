#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="${OUT_DIR:-${ROOT_DIR}/infra/maps/tileserver/fonts}"
FONT_BASE_URL="${FONT_BASE_URL:-https://demotiles.maplibre.org/font}"
EXPECTED_RANGE_COUNT=256

STACKS=(
  "Noto Sans Regular"
  "Noto Sans Bold"
)

usage() {
  cat <<'USAGE'
Usage: build-map-fonts.sh [--force | --verify-only]

Mirrors the MapLibre glyph PBF ranges required by the AOS TileServer style.
The completed font tree is installed atomically so a failed download cannot
replace a previously valid deployment.

Options:
  --force        Re-download and atomically replace an existing valid tree.
  --verify-only  Validate the current local font tree without network access.
USAGE
}

FORCE=false
VERIFY_ONLY=false

case "${1:-}" in
  "")
    ;;
  --force)
    FORCE=true
    ;;
  --verify-only)
    VERIFY_ONLY=true
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: Unknown argument: ${1}" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ $# -gt 1 ]]; then
  echo "ERROR: Too many arguments." >&2
  usage >&2
  exit 2
fi

urlencode_stack() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import quote

print(quote(sys.argv[1], safe=""))
PY
}

validate_pbf() {
  local file="$1"

  if [[ ! -s "$file" ]]; then
    echo "ERROR: Empty or missing glyph file: $file" >&2
    return 1
  fi

  if head -c 64 "$file" | grep -qiE '<!doctype|<html|<\?xml|accessdenied|not found'; then
    echo "ERROR: Non-PBF response stored as glyph data: $file" >&2
    return 1
  fi
}

validate_tree() {
  local root="$1"
  local stack
  local count
  local file

  if [[ ! -d "$root" ]]; then
    echo "ERROR: Map glyph directory is missing: $root" >&2
    return 1
  fi

  for stack in "${STACKS[@]}"; do
    if [[ ! -d "$root/$stack" ]]; then
      echo "ERROR: Map glyph stack is missing: $root/$stack" >&2
      return 1
    fi

    count="$(find "$root/$stack" -maxdepth 1 -type f -name '*-*.pbf' | wc -l | tr -d '[:space:]')"
    if [[ "$count" != "$EXPECTED_RANGE_COUNT" ]]; then
      echo "ERROR: $stack has $count glyph ranges; expected $EXPECTED_RANGE_COUNT." >&2
      return 1
    fi

    for file in \
      "$root/$stack/0-255.pbf" \
      "$root/$stack/8192-8447.pbf" \
      "$root/$stack/65280-65535.pbf"
    do
      validate_pbf "$file" || return 1
    done
  done

  return 0
}

if [[ "$VERIFY_ONLY" == true ]]; then
  validate_tree "$OUT_DIR"
  echo "Map glyph assets are complete: $OUT_DIR"
  exit 0
fi

if [[ "$FORCE" != true ]] && validate_tree "$OUT_DIR" >/dev/null 2>&1; then
  echo "Map glyph assets are already complete: $OUT_DIR"
  exit 0
fi

for command_name in curl python3 find mktemp; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: Required command is not installed: $command_name" >&2
    exit 1
  fi
done

parent_dir="$(dirname "$OUT_DIR")"
mkdir -p "$parent_dir"
staging_dir="$(mktemp -d "${parent_dir}/.fonts-build.XXXXXX")"
backup_dir="${OUT_DIR}.previous"
lock_dir="${parent_dir}/.fonts-build.lock"

cleanup() {
  rm -rf "$staging_dir"
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT

if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "ERROR: Another map-font build appears to be running: $lock_dir" >&2
  exit 1
fi

download_range() {
  local stack="$1"
  local encoded_stack="$2"
  local range="$3"
  local out_file="$4"
  local url="${FONT_BASE_URL%/}/${encoded_stack}/${range}.pbf"

  curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --compressed \
    --retry 4 \
    --retry-all-errors \
    --connect-timeout 10 \
    --max-time 60 \
    "$url" \
    -o "$out_file.tmp"

  validate_pbf "$out_file.tmp"
  mv "$out_file.tmp" "$out_file"
}

echo "==> Mirroring MapLibre glyph assets"
echo "Source: ${FONT_BASE_URL%/}"
echo "Target: $OUT_DIR"

for stack in "${STACKS[@]}"; do
  encoded_stack="$(urlencode_stack "$stack")"
  stack_dir="$staging_dir/$stack"
  mkdir -p "$stack_dir"

  echo "==> Downloading $stack"
  for start in $(seq 0 256 65280); do
    end=$((start + 255))
    range="${start}-${end}"
    download_range "$stack" "$encoded_stack" "$range" "$stack_dir/$range.pbf"
  done
done

# A few mirrors expose pre-compressed PBF bytes without a Content-Encoding
# header. Normalize those files in one process instead of spawning Python for
# every glyph range.
python3 - "$staging_dir" <<'PYCODE'
import gzip
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in root.rglob("*.pbf"):
    data = path.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        path.write_bytes(gzip.decompress(data))
PYCODE

validate_tree "$staging_dir"

rm -rf "$backup_dir"
if [[ -e "$OUT_DIR" ]]; then
  mv "$OUT_DIR" "$backup_dir"
fi

if ! mv "$staging_dir" "$OUT_DIR"; then
  if [[ -e "$backup_dir" && ! -e "$OUT_DIR" ]]; then
    mv "$backup_dir" "$OUT_DIR"
  fi
  exit 1
fi

rm -rf "$backup_dir"
trap - EXIT
rmdir "$lock_dir" 2>/dev/null || true

validate_tree "$OUT_DIR"
echo "==> Map glyph assets installed successfully"
echo "Glyph files: $(find "$OUT_DIR" -type f -name '*.pbf' | wc -l | tr -d '[:space:]')"
