#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

PBF_PATH="${PBF_PATH:-$ROOT_DIR/maps/kenya/kenya.osm.pbf}"
WORK_DIR="${WORK_DIR:-$ROOT_DIR/maps/building-labels}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/maps/tiles}"

NAMED_OSM="$WORK_DIR/named_features.osm.pbf"
NAMED_GEOJSON="$WORK_DIR/named_features.geojson"
BUILDING_GEOJSON="$WORK_DIR/aos_building_labels.geojson"
OUT_MBTILES="$OUT_DIR/aos_building_labels.mbtiles"

echo "==> AOS named building labels build"
echo "ROOT_DIR: $ROOT_DIR"
echo "PBF_PATH: $PBF_PATH"
echo "WORK_DIR: $WORK_DIR"
echo "OUT_MBTILES: $OUT_MBTILES"

for bin in osmium jq tippecanoe; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: Missing required command: $bin" >&2
    exit 1
  fi
done

if [ ! -f "$PBF_PATH" ]; then
  echo "ERROR: Missing Kenya PBF: $PBF_PATH" >&2
  echo "Run ./infra/maps/scripts/prepare-kenya.sh first." >&2
  exit 1
fi

mkdir -p "$WORK_DIR" "$OUT_DIR"

echo "==> Extracting named OSM features"
# We first extract named nodes/ways/relations, then filter down to named buildings in GeoJSON.
# This avoids depending on Planetiler internals and keeps this labels tileset separate.
osmium tags-filter \
  "$PBF_PATH" \
  n/name w/name r/name \
  -o "$NAMED_OSM" \
  --overwrite

echo "==> Exporting named features to GeoJSON"
osmium export \
  "$NAMED_OSM" \
  -o "$NAMED_GEOJSON" \
  --overwrite

echo "==> Filtering named buildings"
jq '
  {
    type: "FeatureCollection",
    features: [
      .features[]
      | select(.properties != null)
      | select(.properties.name != null or .properties["name:en"] != null or .properties["name:latin"] != null)
      | select(.properties.building != null or .properties["building:part"] != null)
      | {
          type: "Feature",
          geometry: .geometry,
          properties: {
            name: (.properties["name:en"] // .properties["name:latin"] // .properties.name),
            name_en: (.properties["name:en"] // null),
            name_latin: (.properties["name:latin"] // null),
            building: (.properties.building // .properties["building:part"] // null)
          }
        }
    ]
  }
' "$NAMED_GEOJSON" > "$BUILDING_GEOJSON"

COUNT="$(jq '.features | length' "$BUILDING_GEOJSON")"
echo "==> Named building features: $COUNT"

if [ "$COUNT" -eq 0 ]; then
  echo "ERROR: No named buildings found. Check whether OSM extract has named buildings." >&2
  exit 1
fi

echo "==> Building MBTiles with tippecanoe"
rm -f "$OUT_MBTILES"

tippecanoe \
  -o "$OUT_MBTILES" \
  -l aos_building_label \
  --minimum-zoom=13 \
  --maximum-zoom=14 \
  --no-feature-limit \
  --no-tile-size-limit \
  --force \
  "$BUILDING_GEOJSON"

echo "==> Built:"
ls -lh "$OUT_MBTILES"

echo "==> Done"
