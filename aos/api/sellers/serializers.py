"""
Seller serializers.
"""

from __future__ import annotations

import math
from typing import Any


def serialize_seller_location(
    seller,
) -> dict:
    """
    Serialize a seller's complete public location.

    Used by:
    - set_my_seller_location
    - remove_my_seller_location
    - get_my_seller_location
    - get_seller_location
    - get_seller
    """

    latitude = _to_optional_float(
        seller.get("latitude")
    )
    longitude = _to_optional_float(
        seller.get("longitude")
    )

    display_address = _normalize_optional_string(
        seller.get("display_address")
    )

    country_code = _normalize_country_code(
        seller.get("country_code")
    )

    has_location = bool(
        seller.get("has_location")
    )

    is_valid_location = (
        has_location
        and latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and display_address is not None
        and country_code is not None
    )

    if not is_valid_location:
        return _empty_location_payload()

    return {
        "has_location": True,
        "name": _normalize_optional_string(
            seller.get("location_name")
        ),
        "latitude": latitude,
        "longitude": longitude,
        "display_address": display_address,
        "locality": _normalize_optional_string(
            seller.get("locality")
        ),
        "region": _normalize_optional_string(
            seller.get("region")
        ),
        "country_code": country_code,
        "instructions": _normalize_optional_string(
            seller.get("location_instructions")
        ),
        "updated_at": seller.get(
            "location_updated_at"
        ),
    }


def serialize_lightweight_seller_location(
    seller,
) -> dict:
    """
    Serialize lightweight seller location data for discovery.

    Exact coordinates, full address, seller instructions, and update
    timestamp are intentionally excluded.
    """

    if not bool(
        seller.get("has_location")
    ):
        return {
            "has_location": False,
        }

    country_code = _normalize_country_code(
        seller.get("country_code")
    )

    if not country_code:
        return {
            "has_location": False,
        }

    return {
        "has_location": True,
        "name": _normalize_optional_string(
            seller.get("location_name")
        ),
        "locality": _normalize_optional_string(
            seller.get("locality")
        ),
        "region": _normalize_optional_string(
            seller.get("region")
        ),
        "country_code": country_code,
    }


def _empty_location_payload() -> dict:
    """Return the standard empty seller-location payload."""

    return {
        "has_location": False,
        "name": None,
        "latitude": None,
        "longitude": None,
        "display_address": None,
        "locality": None,
        "region": None,
        "country_code": None,
        "instructions": None,
        "updated_at": None,
    }


def _to_optional_float(
    value: Any,
) -> float | None:
    """Safely convert a value into a finite float."""

    if value is None or value == "":
        return None

    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(normalized):
        return None

    return normalized


def _normalize_country_code(
    value: Any,
) -> str | None:
    """Normalize an optional ISO 3166-1 alpha-2 country code."""

    normalized = _normalize_optional_string(
        value
    )

    if not normalized:
        return None

    normalized = normalized.upper()

    if (
        len(normalized) != 2
        or not normalized.isalpha()
    ):
        return None

    return normalized


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
