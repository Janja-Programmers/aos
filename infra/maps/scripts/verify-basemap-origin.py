#!/usr/bin/env python3
"""Verify the public AOS PMTiles origin and production basemap contract end to end."""
from __future__ import annotations

import json
import os
import re
import struct
from typing import NoReturn
from urllib.parse import urljoin

import requests

PMTILES_HEADER_BYTES = 127
PMTILES_V3 = 3
PMTILES_MVT = 1
DEFAULT_GLOBAL_MIN_BYTES = 1_048_576


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def _u64(header: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", header, offset)[0]


def _i32(header: bytes, offset: int) -> int:
    return struct.unpack_from("<i", header, offset)[0]


def _parse_pmtiles_header(content: bytes) -> dict[str, object]:
    if len(content) < PMTILES_HEADER_BYTES:
        fail("PMTiles range response is shorter than the 127-byte v3 header")
    header = content[:PMTILES_HEADER_BYTES]
    if header[:7] != b"PMTiles":
        fail("PMTiles range response does not contain the PMTiles header magic")
    spec_version = header[7]
    if spec_version != PMTILES_V3:
        fail(f"Unsupported PMTiles spec version {spec_version}; expected v3")

    parsed = {
        "spec_version": spec_version,
        "root_directory_offset": _u64(header, 8),
        "root_directory_length": _u64(header, 16),
        "metadata_offset": _u64(header, 24),
        "metadata_length": _u64(header, 32),
        "leaf_directory_offset": _u64(header, 40),
        "leaf_directory_length": _u64(header, 48),
        "tile_data_offset": _u64(header, 56),
        "tile_data_length": _u64(header, 64),
        "num_addressed_tiles": _u64(header, 72),
        "num_tile_entries": _u64(header, 80),
        "num_tile_contents": _u64(header, 88),
        "tile_type": header[99],
        "min_zoom": header[100],
        "max_zoom": header[101],
        "min_lon": _i32(header, 102) / 10_000_000,
        "min_lat": _i32(header, 106) / 10_000_000,
        "max_lon": _i32(header, 110) / 10_000_000,
        "max_lat": _i32(header, 114) / 10_000_000,
    }
    return parsed


def _content_range_total(value: str) -> int:
    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", value.strip(), flags=re.IGNORECASE)
    if not match:
        fail("PMTiles response is missing a valid Content-Range header")
    return int(match.group(1))


def _validate_archive(header: dict[str, object], total_bytes: int) -> None:
    min_bytes = int(os.environ.get("MAPS_GLOBAL_BASEMAP_MIN_BYTES", str(DEFAULT_GLOBAL_MIN_BYTES)))
    if total_bytes < min_bytes:
        fail(
            f"PMTiles archive is only {total_bytes} bytes; production global basemap requires at least "
            f"{min_bytes} bytes. A smoke/test PMTiles object appears to be active."
        )

    tile_data_offset = int(header["tile_data_offset"])
    tile_data_length = int(header["tile_data_length"])
    if tile_data_length <= 0:
        fail("PMTiles archive contains no tile data")
    if tile_data_offset + tile_data_length > total_bytes:
        fail("PMTiles tile-data section extends beyond the advertised archive size")
    if int(header["root_directory_offset"]) + int(header["root_directory_length"]) > 16_384:
        fail("PMTiles root directory is not fully contained in the first 16 KiB")
    if int(header["num_tile_entries"]) <= 0 or int(header["num_tile_contents"]) <= 0:
        fail("PMTiles archive reports no tile entries/content")
    if int(header["tile_type"]) != PMTILES_MVT:
        fail("AOS basemap must contain MVT vector tiles")
    if int(header["max_zoom"]) < int(header["min_zoom"]):
        fail("PMTiles zoom bounds are invalid")

    require_global = os.environ.get("MAPS_REQUIRE_GLOBAL_BASEMAP", "1").strip().lower() not in {"0", "false", "no"}
    if require_global:
        min_lon = float(header["min_lon"])
        max_lon = float(header["max_lon"])
        min_lat = float(header["min_lat"])
        max_lat = float(header["max_lat"])
        if min_lon > -170 or max_lon < 170 or min_lat > -80 or max_lat < 80:
            fail(
                "PMTiles bounds do not provide the required global coverage: "
                f"[{min_lon}, {min_lat}, {max_lon}, {max_lat}]"
            )


def main() -> int:
    base = os.environ.get("MAPS_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        fail("MAPS_PUBLIC_BASE_URL is required")
    origin = os.environ.get("MAPS_WEB_ORIGIN", "").strip()
    timeout = float(os.environ.get("MAPS_ORIGIN_VERIFY_TIMEOUT_SECONDS", "10"))

    manifest_url = f"{base}/current.json"
    manifest_headers = {"Accept": "application/json"}
    if origin:
        manifest_headers["Origin"] = origin
    response = requests.get(manifest_url, headers=manifest_headers, timeout=timeout, allow_redirects=False)
    if response.status_code != 200:
        fail(f"Basemap manifest returned HTTP {response.status_code}")
    try:
        pointer = response.json()
    except ValueError:
        fail("Basemap manifest is not valid JSON")
    if not isinstance(pointer, dict):
        fail("Basemap manifest must be an object")
    version = str(pointer.get("version") or "")
    object_name = str(pointer.get("object") or "")
    sha256 = str(pointer.get("sha256") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", version):
        fail("Basemap manifest version is invalid")
    expected_prefix = f"basemap/{version}/"
    if not object_name.startswith(expected_prefix) or not object_name.endswith(".pmtiles"):
        fail("Basemap manifest object is outside the versioned basemap prefix")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        fail("Basemap manifest sha256 is invalid")

    relative = object_name.removeprefix("basemap/")
    object_url = urljoin(base + "/", relative)
    headers = {"Range": "bytes=0-16383", "Accept": "application/octet-stream"}
    if origin:
        headers["Origin"] = origin
    tile = requests.get(object_url, headers=headers, timeout=timeout, allow_redirects=False)
    if tile.status_code != 206:
        fail(f"PMTiles range request returned HTTP {tile.status_code}; expected 206")
    total_bytes = _content_range_total(str(tile.headers.get("Content-Range") or ""))
    if "bytes" not in str(tile.headers.get("Accept-Ranges") or "").lower():
        fail("PMTiles response is missing Accept-Ranges: bytes")
    if origin:
        allow_origin = str(tile.headers.get("Access-Control-Allow-Origin") or "")
        if allow_origin not in {"*", origin}:
            fail("PMTiles response does not allow the configured web origin")

    header = _parse_pmtiles_header(tile.content)
    _validate_archive(header, total_bytes)

    print(json.dumps({
        "ok": True,
        "version": version,
        "object": object_name,
        "manifest_status": response.status_code,
        "range_status": tile.status_code,
        "content_range": tile.headers.get("Content-Range"),
        "cors": tile.headers.get("Access-Control-Allow-Origin"),
        "archive_bytes": total_bytes,
        "tile_data_bytes": header["tile_data_length"],
        "tile_entries": header["num_tile_entries"],
        "min_zoom": header["min_zoom"],
        "max_zoom": header["max_zoom"],
        "bounds": [header["min_lon"], header["min_lat"], header["max_lon"], header["max_lat"]],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
