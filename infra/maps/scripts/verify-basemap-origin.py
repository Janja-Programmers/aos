#!/usr/bin/env python3
"""Verify the public AOS PMTiles origin contract end to end."""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urljoin

import requests


def fail(message: str) -> "NoReturn":
    raise SystemExit(message)


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
    except ValueError as exc:
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
    if not str(tile.headers.get("Content-Range") or "").lower().startswith("bytes 0-"):
        fail("PMTiles response is missing a valid Content-Range header")
    if "bytes" not in str(tile.headers.get("Accept-Ranges") or "").lower():
        fail("PMTiles response is missing Accept-Ranges: bytes")
    if origin:
        allow_origin = str(tile.headers.get("Access-Control-Allow-Origin") or "")
        if allow_origin not in {"*", origin}:
            fail("PMTiles response does not allow the configured web origin")
    if not tile.content.startswith(b"PMTiles"):
        fail("PMTiles range response does not contain the PMTiles header magic")

    print(json.dumps({
        "ok": True,
        "version": version,
        "object": object_name,
        "manifest_status": response.status_code,
        "range_status": tile.status_code,
        "content_range": tile.headers.get("Content-Range"),
        "cors": tile.headers.get("Access-Control-Allow-Origin"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
