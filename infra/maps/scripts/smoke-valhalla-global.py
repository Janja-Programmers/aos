#!/usr/bin/env python3
"""Validate a private Valhalla runtime against representative global routes."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CASES = (
    ("africa-nairobi", "auto", (-1.286389, 36.817223), (-1.292066, 36.821945)),
    ("europe-london", "pedestrian", (51.5074, -0.1278), (51.5155, -0.1410)),
    ("europe-amsterdam", "bicycle", (52.3676, 4.9041), (52.3728, 4.8936)),
    ("asia-tokyo", "auto", (35.6812, 139.7671), (35.6896, 139.7006)),
    ("north-america-new-york", "auto", (40.7580, -73.9855), (40.7484, -73.9857)),
    ("south-america-sao-paulo", "auto", (-23.5505, -46.6333), (-23.5614, -46.6559)),
    ("oceania-sydney", "auto", (-33.8688, 151.2093), (-33.8731, 151.2065)),
)


def request_json(url: str, *, timeout: float, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "AOS-Valhalla-Smoke/1.0"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator supplied private URL
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            decoded = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("response was not a JSON object")
    return decoded


def validate_route(name: str, data: dict) -> None:
    trip = data.get("trip")
    if not isinstance(trip, dict):
        raise RuntimeError(f"{name}: missing trip")
    legs = trip.get("legs")
    if not isinstance(legs, list) or not legs:
        raise RuntimeError(f"{name}: missing route legs")
    summary = trip.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError(f"{name}: missing trip summary")
    length = summary.get("length")
    time = summary.get("time")
    if not isinstance(length, (int, float)) or length <= 0:
        raise RuntimeError(f"{name}: non-positive route length")
    if not isinstance(time, (int, float)) or time <= 0:
        raise RuntimeError(f"{name}: non-positive route time")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]", "http://valhalla", "http://aos-valhalla")):
        raise SystemExit("Refusing non-private/non-loopback Valhalla smoke-test URL")

    status = request_json(f"{base}/status", timeout=args.timeout)
    if not status:
        raise SystemExit("Valhalla /status returned an empty object")
    print("status: PASS")

    for name, costing, origin, destination in CASES:
        payload = {
            "locations": [
                {"lat": origin[0], "lon": origin[1], "type": "break"},
                {"lat": destination[0], "lon": destination[1], "type": "break"},
            ],
            "costing": costing,
            "units": "kilometers",
            "shape_format": "polyline6",
        }
        data = request_json(f"{base}/route", timeout=args.timeout, payload=payload)
        validate_route(name, data)
        print(f"{name} ({costing}): PASS")

    print("VALHALLA GLOBAL ROUTING SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
