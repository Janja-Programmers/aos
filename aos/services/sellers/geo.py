"""Global WGS84 helpers for seller proximity discovery.

Seller discovery owns its query bounding box. Maps owns geocoding/routing and
seller-location APIs; seller list filtering must not depend on provider or
legacy regional-coverage helpers.
"""

from __future__ import annotations

import math

_METERS_PER_DEGREE_KM = 111.32
_LONGITUDE_MIN = -180.0
_LONGITUDE_MAX = 180.0
_LATITUDE_MIN = -90.0
_LATITUDE_MAX = 90.0
_POLE_COSINE_EPSILON = 1e-6


def radius_bbox(*, latitude: float, longitude: float, radius_km: float) -> dict[str, float | bool]:
    """Return a bounded WGS84 prefilter box for a radius search.

    The exact distance filter remains the Haversine expression in the seller
    query. This box only narrows candidate rows and correctly represents
    antimeridian-crossing searches.
    """

    lat_delta = radius_km / _METERS_PER_DEGREE_KM
    south = max(_LATITUDE_MIN, latitude - lat_delta)
    north = min(_LATITUDE_MAX, latitude + lat_delta)

    cosine = abs(math.cos(math.radians(latitude)))
    if cosine < _POLE_COSINE_EPSILON:
        return {
            "south": south,
            "north": north,
            "west": _LONGITUDE_MIN,
            "east": _LONGITUDE_MAX,
            "crosses_antimeridian": False,
        }

    lon_delta = radius_km / (_METERS_PER_DEGREE_KM * cosine)
    if lon_delta >= 180.0:
        return {
            "south": south,
            "north": north,
            "west": _LONGITUDE_MIN,
            "east": _LONGITUDE_MAX,
            "crosses_antimeridian": False,
        }

    raw_west = longitude - lon_delta
    raw_east = longitude + lon_delta
    crosses_antimeridian = raw_west < _LONGITUDE_MIN or raw_east > _LONGITUDE_MAX

    return {
        "south": south,
        "north": north,
        "west": _normalize_longitude(raw_west),
        "east": _normalize_longitude(raw_east),
        "crosses_antimeridian": crosses_antimeridian,
    }


def _normalize_longitude(value: float) -> float:
    normalized = ((value + 180.0) % 360.0) - 180.0
    # Preserve +180 for positive-boundary values so a non-wrapping full-range
    # bound never becomes [-180, -180]. Crossing boxes are explicitly tagged.
    if normalized == -180.0 and value > 0:
        return 180.0
    return normalized
