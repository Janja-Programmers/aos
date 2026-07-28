"""
Seller map points.

Returns seller pins or clusters for the currently visible map viewport.
This endpoint is intentionally map-specific and may return exact seller
coordinates for public pins.
"""

from __future__ import annotations

import math
from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map
from aos.services.sellers.identity import migration_fallback_public_seller_id, normalize_public_seller_id
from aos.api.maps.validators import (
    clamp_bbox_to_supported_area,
    viewport_intersects_supported_area,
)

from .constants import (
    LIST_SELLER_MAP_POINTS_LIMIT_PER_MINUTE_PER_IP,
    SELLER_MAP_POINTS_DEFAULT_ZOOM,
    SELLER_MAP_POINTS_MAX_ITEMS,
    SELLER_MAP_POINTS_MAX_RAW_SELLERS,
    SELLER_MAP_POINTS_MAX_ZOOM,
    SELLER_MAP_POINTS_MIN_ZOOM,
)


VALID_BOOLEAN_FILTERS = {
    "0": 0,
    "1": 1,
    "false": 0,
    "true": 1,
    "no": 0,
    "yes": 1,
}


def list_seller_map_points_impl(**kwargs):
    """Return seller pins or clusters for a map viewport."""

    try:
        ip = request_ip()

        rl = rate_limit(
            key=f"aos:sellers:list_seller_map_points:ip:{ip}",
            ttl_seconds=60,
            limit=LIST_SELLER_MAP_POINTS_LIMIT_PER_MINUTE_PER_IP,
            message="Too many map requests. Please try again shortly.",
        )
        if rl:
            return rl

        viewport, viewport_error = _validate_viewport(
            kwargs
        )
        if viewport_error:
            return viewport_error

        filters, filter_error = _validate_filters(
            kwargs
        )
        if filter_error:
            return filter_error

        viewer = current_user()
        is_logged_in = bool(
            viewer
            and viewer != "Guest"
        )

        conditions = [
            "s.status = 'Active'",
            "u.enabled = 1",
            "COALESCE(p.is_deleted, 0) = 0",
            "COALESCE(p.account_status, 'Active') = 'Active'",
            "COALESCE(s.has_location, 0) = 1",
            "s.latitude IS NOT NULL",
            "s.longitude IS NOT NULL",
            "s.latitude BETWEEN %s AND %s",
            "s.longitude BETWEEN %s AND %s",
        ]

        params: list[Any] = [
            viewport["south"],
            viewport["north"],
            viewport["west"],
            viewport["east"],
        ]

        if is_logged_in:
            conditions.append("s.user != %s")
            params.append(viewer)
            conditions.append(
                """
                NOT EXISTS (
                    SELECT 1 FROM `tabAOS User Block` b
                    WHERE b.status = 'Active'
                      AND ((b.blocker_user = %s AND b.blocked_user = s.user)
                        OR (b.blocked_user = %s AND b.blocker_user = s.user))
                )
                """
            )
            params.extend([viewer, viewer])

        if filters.get("seller_type"):
            conditions.append(
                "s.seller_type = %s"
            )
            params.append(
                filters["seller_type"]
            )

        if filters.get("business_category"):
            conditions.append(
                "s.business_category = %s"
            )
            params.append(
                filters["business_category"]
            )

        if filters.get("is_verified") is not None:
            conditions.append(
                "COALESCE(p.is_verified, 0) = %s"
            )
            params.append(
                filters["is_verified"]
            )

        where_clause = " AND ".join(
            conditions
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name,
                s.public_id,
                s.user,
                s.business_category,
                s.seller_type,
                s.location_name,
                s.locality,
                s.region,
                s.country_code,
                s.latitude,
                s.longitude,
                COALESCE(p.is_verified, 0) AS is_verified,
                u.full_name,
                u.user_image

            FROM `tabAOS Seller` s

            INNER JOIN `tabUser` u
                ON u.name = s.user

            INNER JOIN `tabAOS Profile` p
                ON p.user = s.user

            WHERE {where_clause}

            ORDER BY
                COALESCE(p.is_verified, 0) DESC,
                s.creation DESC

            LIMIT %s
            """,
            (
                *params,
                SELLER_MAP_POINTS_MAX_RAW_SELLERS,
            ),
            as_dict=True,
        )

        if _should_cluster(
            zoom=viewport["zoom"],
            row_count=len(rows),
        ):
            items = _cluster_rows(
                rows=rows,
                zoom=viewport["zoom"],
            )
            response_type = "clusters"
        else:
            display_by_user = get_user_display_map(
                [
                    row.get("user")
                    for row in rows[:SELLER_MAP_POINTS_MAX_ITEMS]
                    if row.get("user")
                ]
            )
            items = [
                _serialize_pin(
                    row=row,
                    display_by_user=display_by_user,
                )
                for row in rows[:SELLER_MAP_POINTS_MAX_ITEMS]
            ]
            response_type = "pins"

        return ok(
            "Seller map points fetched successfully.",
            data={
                "items": items[:SELLER_MAP_POINTS_MAX_ITEMS],
                "count": min(
                    len(items),
                    SELLER_MAP_POINTS_MAX_ITEMS,
                ),
                "type": response_type,
                "zoom": viewport["zoom"],
                "truncated": len(rows) >= SELLER_MAP_POINTS_MAX_RAW_SELLERS,
                "viewport": {
                    "north": viewport["north"],
                    "south": viewport["south"],
                    "east": viewport["east"],
                    "west": viewport["west"],
                },
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Seller Map Points Failed",
        )

        return fail(
            "Failed to fetch seller map points.",
            error="INTERNAL_ERROR",
        )


def _validate_viewport(
    kwargs: dict,
):
    """
    Validate map viewport bounds.

    Returns:
        tuple[dict | None, response | None]
    """

    north, north_error = _parse_float(
        kwargs.get("north"),
        label="north",
        minimum=-90,
        maximum=90,
    )
    if north_error:
        return None, north_error

    south, south_error = _parse_float(
        kwargs.get("south"),
        label="south",
        minimum=-90,
        maximum=90,
    )
    if south_error:
        return None, south_error

    east, east_error = _parse_float(
        kwargs.get("east"),
        label="east",
        minimum=-180,
        maximum=180,
    )
    if east_error:
        return None, east_error

    west, west_error = _parse_float(
        kwargs.get("west"),
        label="west",
        minimum=-180,
        maximum=180,
    )
    if west_error:
        return None, west_error

    if north <= south:
        return (
            None,
            fail(
                "north must be greater than south.",
                error="VALIDATION_ERROR",
            ),
        )

    if east <= west:
        return (
            None,
            fail(
                "east must be greater than west.",
                error="VALIDATION_ERROR",
            ),
        )

    zoom = _parse_zoom(
        kwargs.get("zoom")
    )

    if zoom is None:
        zoom = SELLER_MAP_POINTS_DEFAULT_ZOOM

    if not viewport_intersects_supported_area(
        north=north,
        south=south,
        east=east,
        west=west,
    ):
        return (
            None,
            fail(
                "Viewport is outside the supported AOS Maps coverage area.",
                error="VALIDATION_ERROR",
            ),
        )

    clipped_viewport = clamp_bbox_to_supported_area(
        north=north,
        south=south,
        east=east,
        west=west,
    )
    if not clipped_viewport:
        return (
            None,
            fail(
                "Viewport is outside the supported AOS Maps coverage area.",
                error="VALIDATION_ERROR",
            ),
        )

    north = clipped_viewport["north"]
    south = clipped_viewport["south"]
    east = clipped_viewport["east"]
    west = clipped_viewport["west"]

    lat_span = north - south
    lon_span = east - west

    if zoom >= 14 and (lat_span > 1.0 or lon_span > 1.0):
        return (
            None,
            fail(
                "Viewport is too large for this zoom level.",
                error="VALIDATION_ERROR",
            ),
        )

    if lat_span > 12 or lon_span > 12:
        return (
            None,
            fail(
                "Viewport is too large.",
                error="VALIDATION_ERROR",
            ),
        )

    return {
        "north": round(north, 7),
        "south": round(south, 7),
        "east": round(east, 7),
        "west": round(west, 7),
        "zoom": zoom,
    }, None


def _validate_filters(
    kwargs: dict,
):
    """Validate optional seller map filters."""

    seller_type = _normalize_optional_string(
        kwargs.get("seller_type")
    )

    business_category = _normalize_optional_string(
        kwargs.get("business_category")
        or kwargs.get("category")
    )

    is_verified, is_verified_error = _parse_optional_boolean(
        kwargs.get("is_verified"),
        field_label="is_verified",
    )
    if is_verified_error:
        return None, is_verified_error

    return {
        "seller_type": seller_type,
        "business_category": business_category,
        "is_verified": is_verified,
    }, None


def _parse_optional_boolean(
    value: Any,
    *,
    field_label: str,
):
    """Parse an optional boolean value."""

    if value is None or value == "":
        return None, None

    if isinstance(value, bool):
        return int(value), None

    if isinstance(value, int) and value in {0, 1}:
        return value, None

    normalized = str(value).strip().lower()

    if normalized not in VALID_BOOLEAN_FILTERS:
        return (
            None,
            fail(
                f"{field_label} must be true or false.",
                error="VALIDATION_ERROR",
            ),
        )

    return VALID_BOOLEAN_FILTERS[normalized], None


def _parse_float(
    value: Any,
    *,
    label: str,
    minimum: float,
    maximum: float,
):
    """Parse and validate a required float."""

    if value is None or str(value).strip() == "":
        return (
            None,
            fail(
                f"{label} is required.",
                error="VALIDATION_ERROR",
            ),
        )

    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return (
            None,
            fail(
                f"{label} must be a valid number.",
                error="VALIDATION_ERROR",
            ),
        )

    if not math.isfinite(normalized):
        return (
            None,
            fail(
                f"{label} must be a finite number.",
                error="VALIDATION_ERROR",
            ),
        )

    if normalized < minimum or normalized > maximum:
        return (
            None,
            fail(
                f"{label} must be between {minimum:g} and {maximum:g}.",
                error="VALIDATION_ERROR",
            ),
        )

    return normalized, None


def _parse_zoom(
    value: Any,
) -> int | None:
    """Parse map zoom."""

    if value is None or value == "":
        return None

    try:
        zoom = int(float(value))
    except (TypeError, ValueError):
        return SELLER_MAP_POINTS_DEFAULT_ZOOM

    return max(
        SELLER_MAP_POINTS_MIN_ZOOM,
        min(
            zoom,
            SELLER_MAP_POINTS_MAX_ZOOM,
        ),
    )


def _should_cluster(
    *,
    zoom: int,
    row_count: int,
) -> bool:
    """Decide whether to return clusters instead of individual pins."""

    return zoom < 14 or row_count > SELLER_MAP_POINTS_MAX_ITEMS


def _cluster_rows(
    *,
    rows: list[dict],
    zoom: int,
) -> list[dict]:
    """Cluster seller rows into grid buckets."""

    grid_size = _grid_size_for_zoom(
        zoom
    )

    buckets: dict[tuple[int, int], dict] = {}

    for row in rows:
        latitude = _to_optional_float(
            row.get("latitude")
        )
        longitude = _to_optional_float(
            row.get("longitude")
        )

        if latitude is None or longitude is None:
            continue

        key = (
            math.floor(latitude / grid_size),
            math.floor(longitude / grid_size),
        )

        bucket = buckets.setdefault(
            key,
            {
                "count": 0,
                "lat_sum": 0.0,
                "lon_sum": 0.0,
            },
        )

        bucket["count"] += 1
        bucket["lat_sum"] += latitude
        bucket["lon_sum"] += longitude

    clusters = []

    for bucket in buckets.values():
        count = int(bucket["count"])

        if count <= 0:
            continue

        clusters.append(
            {
                "type": "cluster",
                "latitude": round(
                    bucket["lat_sum"] / count,
                    7,
                ),
                "longitude": round(
                    bucket["lon_sum"] / count,
                    7,
                ),
                "count": count,
            }
        )

    clusters.sort(
        key=lambda item: item["count"],
        reverse=True,
    )

    return clusters


def _grid_size_for_zoom(
    zoom: int,
) -> float:
    """Return approximate grid size in degrees."""

    if zoom <= 7:
        return 0.5

    if zoom <= 9:
        return 0.25

    if zoom <= 11:
        return 0.12

    if zoom <= 13:
        return 0.05

    return 0.02


def _serialize_pin(
    *,
    row: dict,
    display_by_user: dict[str, dict[str, Any]],
) -> dict:
    """Serialize a public seller map pin."""

    display = display_by_user.get(
        row.get("user")
    ) or {}

    return {
        "type": "seller",
        "seller": normalize_public_seller_id(row.get("public_id")) or migration_fallback_public_seller_id(row.get("name")),
        "seller_id": normalize_public_seller_id(row.get("public_id")) or migration_fallback_public_seller_id(row.get("name")),
        "user": display.get("user"),
        "display_name": display.get("display_name") or row.get("full_name"),
        "avatar": display.get("avatar") or row.get("user_image"),
        "business_category": row.get("business_category"),
        "seller_type": row.get("seller_type"),
        "is_verified": bool(row.get("is_verified")),
        "latitude": _to_optional_float(
            row.get("latitude")
        ),
        "longitude": _to_optional_float(
            row.get("longitude")
        ),
        "location_name": _normalize_optional_string(
            row.get("location_name")
        ),
        "locality": _normalize_optional_string(
            row.get("locality")
        ),
        "region": _normalize_optional_string(
            row.get("region")
        ),
        "country_code": _normalize_optional_string(
            row.get("country_code")
        ),
    }


def _to_optional_float(
    value: Any,
) -> float | None:
    """Convert a finite value to float."""

    if value is None or value == "":
        return None

    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(normalized):
        return None

    return normalized


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
