#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/infra/maps/tileserver/fonts}"

# We mirror known-good MapLibre glyph PBF files once, then serve them ourselves.
FONT_BASE_URL="${FONT_BASE_URL:-https://demotiles.maplibre.org/font}"

STACKS=(
  "Noto Sans Regular"
  "Noto Sans Bold"
)

mkdir -p "$OUT_DIR"

echo "==> Mirroring map glyph fonts"
echo "FONT_BASE_URL: $FONT_BASE_URL"
echo "OUT_DIR: $OUT_DIR"

urlencode_stack() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=""))
PY
}

validate_pbf() {
  local file="$1"

  if [ ! -s "$file" ]; then
    echo "ERROR: Empty glyph file: $file" >&2
    return 1
  fi

  # Reject HTML/XML error pages saved as .pbf.
  if head -c 32 "$file" | grep -qiE '<!doctype|<html|<\?xml'; then
    echo "ERROR: Downloaded HTML/XML instead of glyph PBF: $file" >&2
    echo "First bytes:" >&2
    xxd -l 64 "$file" >&2 || true
    return 1
  fi

  return 0
}

download_range() {
  local stack="$1"
  local encoded_stack="$2"
  local range="$3"
  local out_file="$4"

  local url="$FONT_BASE_URL/$encoded_stack/$range.pbf"

  curl -fsSL --retry 3 --retry-delay 1 "$url" -o "$out_file.tmp"

  # If upstream sends gzip bytes without us wanting them stored compressed,
  # decompress before saving. Otherwise keep raw PBF.
  python3 - "$out_file.tmp" <<'PY'
import gzip
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()

if data.startswith(b"\x1f\x8b"):
    path.write_bytes(gzip.decompress(data))
PY

  validate_pbf "$out_file.tmp"
  mv "$out_file.tmp" "$out_file"
}

# Remove previous bad files first.
for stack in "${STACKS[@]}"; do
  rm -rf "${OUT_DIR:?}/${stack}"
done

for stack in "${STACKS[@]}"; do
  encoded_stack="$(urlencode_stack "$stack")"
  stack_dir="$OUT_DIR/$stack"
  mkdir -p "$stack_dir"

  echo "==> Downloading $stack"

  for start in $(seq 0 256 65280); do
    end=$((start + 255))
    range="${start}-${end}"
    download_range "$stack" "$encoded_stack" "$range" "$stack_dir/$range.pbf"
  done
done

echo "==> Done"
echo "Generated PBF files:"
find "$OUT_DIR" -type f -name "*.pbf" | wc -l

echo "Sample:"
find "$OUT_DIR" -maxdepth 2 -type f -name "*.pbf" | sort | head -10
