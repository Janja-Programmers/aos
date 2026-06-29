#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/infra/maps/tileserver/fonts}"

FONT_BASE_URL="${FONT_BASE_URL:-https://fonts.openmaptiles.org}"

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

download_range() {
  local stack="$1"
  local encoded_stack="$2"
  local range="$3"
  local out_file="$4"

  local url="$FONT_BASE_URL/$encoded_stack/$range.pbf"

  if [ -s "$out_file" ]; then
    return 0
  fi

  curl -fsSL "$url" -o "$out_file.tmp"
  mv "$out_file.tmp" "$out_file"
}

for stack in "${STACKS[@]}"; do
  encoded_stack="$(urlencode_stack "$stack")"
  stack_dir="$OUT_DIR/$stack"
  mkdir -p "$stack_dir"

  echo "==> Downloading $stack"

  # Common MapLibre glyph ranges are 256-codepoint files:
  # 0-255.pbf, 256-511.pbf, ... 65280-65535.pbf
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
