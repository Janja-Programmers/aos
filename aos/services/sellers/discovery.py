"""Bounded public Seller discovery with stable keyset pagination."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.sql_safety import safe_like_contains
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map as social_relationship_map

from .constants import (
    LIST_ALLOWED_FIELDS,
    NEARBY_SELLERS_DEFAULT_RADIUS_KM,
    NEARBY_SELLERS_MAX_RADIUS_KM,
    NEARBY_SELLERS_MIN_RADIUS_KM,
    STATUS_ACTIVE,
)
from .errors import SellerValidationError
from .geo import radius_bbox
from .observability import seller_log
from .pagination import decode_cursor, encode_cursor, query_fingerprint
from .serializers import display_map, guest_relationship, serialize_public_list_item
from .validation import (
    ensure_known_fields,
    normalize_business_category,
    normalize_coordinate,
    normalize_country_code,
    normalize_follow_filter,
    normalize_location_filter,
    normalize_optional_boolean,
    normalize_pagination,
    normalize_radius,
    normalize_search,
    normalize_seller_type,
    normalize_sort,
)

_EARTH_RADIUS_KM = 6371.0

_SORTS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "recommended": (
        ["verified_sort", "rating_sort", "followers_sort", "creation", "name"],
        ["DESC", "DESC", "DESC", "DESC", "ASC"],
        ["verified_sort", "rating_sort", "followers_sort", "creation", "name"],
    ),
    "rating": (["rating_sort", "reviews_sort", "name"], ["DESC", "DESC", "ASC"], ["rating_sort", "reviews_sort", "name"]),
    "newest": (["creation", "name"], ["DESC", "DESC"], ["creation", "name"]),
    "most_ads": (["ads_sort", "rating_sort", "name"], ["DESC", "DESC", "ASC"], ["ads_sort", "rating_sort", "name"]),
    "most_reviewed": (["reviews_sort", "rating_sort", "name"], ["DESC", "DESC", "ASC"], ["reviews_sort", "rating_sort", "name"]),
    "nearest": (["distance_km", "verified_sort", "rating_sort", "name"], ["ASC", "DESC", "DESC", "ASC"], ["distance_km", "verified_sort", "rating_sort", "name"]),
}

_ALIAS_EXPR = {
    "verified_sort": "CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END",
    "rating_sort": "COALESCE(s.rating, 0)",
    "followers_sort": "COALESCE(p.total_followers, 0)",
    "reviews_sort": "COALESCE(s.total_reviews, 0)",
    "ads_sort": "COALESCE(s.total_ads, 0)",
    "creation": "s.creation",
    "name": "s.name",
}


class SellerDiscoveryService:
    def list_public(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, LIST_ALLOWED_FIELDS)
        authenticated = bool(viewer and viewer != "Guest")
        limit, cursor = normalize_pagination(request)
        search = normalize_search(request.get("search"))
        seller_type = normalize_seller_type(request.get("seller_type"))
        business_category = normalize_business_category(request.get("business_category"))
        follow_filter = normalize_follow_filter(request.get("follow_filter"), authenticated=authenticated)
        locality = normalize_location_filter(request.get("locality"), field="locality")
        region = normalize_location_filter(request.get("region"), field="region")
        country_code = normalize_country_code(request.get("country_code"))
        verified = normalize_optional_boolean(request.get("is_verified"), field="is_verified")
        has_location = normalize_optional_boolean(request.get("has_location"), field="has_location")
        sort = normalize_sort(request.get("sort"))
        geo = self._geo_context(request)
        if sort == "nearest" and not geo:
            raise SellerValidationError(
                "Latitude and longitude are required for nearest sorting.", code="INVALID_SELLER_LOCATION"
            )
        if geo and has_location is None:
            has_location = 1

        query_shape = {
            "search": search,
            "seller_type": seller_type,
            "business_category": business_category,
            "follow_filter": follow_filter,
            "locality": locality,
            "region": region,
            "country_code": country_code,
            "is_verified": verified,
            "has_location": has_location,
            "sort": sort,
            "latitude": geo["latitude"] if geo else None,
            "longitude": geo["longitude"] if geo else None,
            "radius_km": geo["radius_km"] if geo else None,
            # Pagination results depend on the exact viewer because self-exclusion,
            # blocks and follow filters are viewer-specific. query_fingerprint hashes
            # this value before anything is emitted in the cursor, so the account
            # identifier is never exposed to the client.
            "viewer_scope": str(viewer) if authenticated else "guest",
        }
        query_key = query_fingerprint(query_shape)
        aliases, directions, cursor_keys = _SORTS[sort]
        cursor_values = decode_cursor(cursor, sort=sort, query_key=query_key, expected_keys=len(cursor_keys))

        conditions = [
            "s.status = %s",
            "u.enabled = 1",
            "COALESCE(p.account_status, 'Active') = 'Active'",
        ]
        params: list[Any] = [STATUS_ACTIVE]
        if authenticated:
            conditions.append("s.user != %s")
            params.append(viewer)
            conditions.append(
                """NOT EXISTS (
                    SELECT 1 FROM `tabAOS User Block` b
                    WHERE b.status = 'Active'
                      AND ((b.blocker_user = %s AND b.blocked_user = s.user)
                        OR (b.blocked_user = %s AND b.blocker_user = s.user))
                )"""
            )
            params.extend([viewer, viewer])
        if verified is not None:
            conditions.append("CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END = %s")
            params.append(verified)
        if has_location is not None:
            conditions.append("COALESCE(s.has_location, 0) = %s")
            params.append(has_location)
        for expression, value in (("s.seller_type", seller_type), ("s.business_category", business_category), ("s.locality", locality), ("s.region", region), ("s.country_code", country_code)):
            if value:
                conditions.append(f"{expression} = %s")
                params.append(value)
        if search:
            escaped = safe_like_contains(search)
            conditions.append(
                """(p.display_name LIKE %s ESCAPE '\\\\' OR s.business_category LIKE %s ESCAPE '\\\\'
                    OR s.about_business LIKE %s ESCAPE '\\\\' OR s.location_name LIKE %s ESCAPE '\\\\'
                    OR s.display_address LIKE %s ESCAPE '\\\\' OR s.locality LIKE %s ESCAPE '\\\\'
                    OR s.region LIKE %s ESCAPE '\\\\')"""
            )
            params.extend([escaped] * 7)
        if follow_filter:
            operator = "EXISTS" if follow_filter == "following" else "NOT EXISTS"
            conditions.append(
                f"{operator} (SELECT 1 FROM `tabAOS Follow` f WHERE f.follower_user = %s AND f.following_user = s.user)"
            )
            params.append(viewer)

        select_params: list[Any] = []
        having_parts: list[str] = []
        having_params: list[Any] = []
        distance_select = "NULL AS distance_km"
        if geo:
            conditions.extend([
                "COALESCE(s.has_location, 0) = 1",
                "s.latitude IS NOT NULL", "s.longitude IS NOT NULL",
                "s.latitude BETWEEN %s AND %s",
            ])
            params.extend([geo["south"], geo["north"]])
            if geo["crosses_antimeridian"]:
                conditions.append("(s.longitude >= %s OR s.longitude <= %s)")
            else:
                conditions.append("s.longitude BETWEEN %s AND %s")
            params.extend([geo["west"], geo["east"]])
            distance_select = self._distance_expression()
            select_params.extend([geo["latitude"], geo["latitude"], geo["longitude"]])
            having_parts.append("distance_km <= %s")
            having_params.append(geo["radius_km"])

        if cursor_values:
            expressions = ["distance_km" if alias == "distance_km" else _ALIAS_EXPR[alias] for alias in aliases]
            clause, values = self._keyset_clause(expressions, directions, cursor_values)
            if sort == "nearest":
                having_parts.append(clause)
                having_params.extend(values)
            else:
                conditions.append(clause)
                params.extend(values)

        order_by = ", ".join(f"{alias} {direction}" for alias, direction in zip(aliases, directions))
        having = f"HAVING {' AND '.join(having_parts)}" if having_parts else ""
        rows = frappe.db.sql(
            f"""
            SELECT s.name, s.public_id, s.user, s.seller_type, s.business_category,
                   s.shop_banner_media, s.rating, s.total_reviews, s.total_ads, s.creation,
                   s.has_location, s.location_name, s.locality, s.region, s.country_code,
                   s.latitude, s.longitude,
                   COALESCE(p.total_followers, 0) AS total_followers,
                   COALESCE(p.total_following, 0) AS total_following,
                   CASE WHEN v.status = 'Approved' THEN 1 ELSE 0 END AS verified_sort,
                   COALESCE(s.rating, 0) AS rating_sort,
                   COALESCE(p.total_followers, 0) AS followers_sort,
                   COALESCE(s.total_reviews, 0) AS reviews_sort,
                   COALESCE(s.total_ads, 0) AS ads_sort,
                   v.verification_type, v.status AS verification_status,
                   {distance_select}
            FROM `tabAOS Seller` s
            INNER JOIN `tabUser` u ON u.name = s.user
            INNER JOIN `tabAOS Profile` p ON p.user = s.user
            LEFT JOIN `tabAOS Verification Request` v ON v.user = s.user
            WHERE {' AND '.join(conditions)}
            {having}
            ORDER BY {order_by}
            LIMIT %s
            """,
            (*select_params, *params, *having_params, limit + 1),
            as_dict=True,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        users = [str(row.user) for row in rows if row.user]
        displays = display_map(users)
        relationships = self._relationship_map(viewer=viewer if authenticated else None, targets=users, displays=displays)
        friend_counts = self._friend_counts(users)
        items: list[dict[str, Any]] = []
        for row in rows:
            row.total_friends = friend_counts.get(row.user, 0)
            items.append(
                serialize_public_list_item(
                    row,
                    display=displays.get(row.user) or {},
                    relationship=relationships.get(row.user) or guest_relationship(target_user=(displays.get(row.user) or {}).get("account_id")),
                )
            )
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            values = [last.get(key) for key in cursor_keys]
            next_cursor = encode_cursor(sort=sort, query_key=query_key, values=values)
        result: dict[str, Any] = {
            "items": items,
            "limit": limit,
            "count": len(items),
            "has_more": has_more,
            "next_cursor": next_cursor,
            "sort": sort,
        }
        if geo:
            result["nearby"] = {"latitude": geo["latitude"], "longitude": geo["longitude"], "radius_km": geo["radius_km"], "sort": sort}
        seller_log("seller.listed", operation="list_public", count=len(items))
        return result

    @staticmethod
    def _keyset_clause(expressions: list[str], directions: list[str], values: list[Any]) -> tuple[str, list[Any]]:
        branches: list[str] = []
        params: list[Any] = []
        for index, (expression, direction, value) in enumerate(zip(expressions, directions, values)):
            prefix = []
            for previous in range(index):
                prefix.append(f"{expressions[previous]} = %s")
                params.append(values[previous])
            operator = ">" if direction == "ASC" else "<"
            comparison = f"{expression} {operator} %s"
            params.append(value)
            branches.append("(" + " AND ".join([*prefix, comparison]) + ")")
        return "(" + " OR ".join(branches) + ")", params

    @staticmethod
    def _friend_counts(users: list[str]) -> dict[str, int]:
        unique = sorted(set(users))
        if not unique:
            return {}
        rows = frappe.db.sql(
            """SELECT outgoing.follower_user AS user, COUNT(*) AS total_friends
               FROM `tabAOS Follow` outgoing
               INNER JOIN `tabAOS Follow` incoming
                 ON incoming.follower_user = outgoing.following_user
                AND incoming.following_user = outgoing.follower_user
               WHERE outgoing.follower_user IN %(users)s GROUP BY outgoing.follower_user""",
            {"users": tuple(unique)}, as_dict=True,
        )
        return {str(row.user): max(0, int(row.total_friends or 0)) for row in rows}

    @staticmethod
    def _relationship_map(*, viewer: str | None, targets: list[str], displays: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        unique = sorted(set(targets))
        if not viewer or not unique:
            return {target: guest_relationship(target_user=(displays.get(target) or {}).get("account_id")) for target in unique}
        return social_relationship_map(repository=SocialRepository(), viewer=viewer, targets=unique)

    @staticmethod
    def _geo_context(request: dict[str, Any]) -> dict[str, float | bool] | None:
        lat_value = request.get("latitude")
        lon_value = request.get("longitude")
        radius_value = request.get("radius_km")
        if lat_value in (None, "") and lon_value in (None, "") and radius_value in (None, ""):
            return None
        if lat_value in (None, "") or lon_value in (None, ""):
            raise SellerValidationError("Latitude and longitude are required for nearby seller discovery.", code="INVALID_SELLER_LOCATION")
        latitude = normalize_coordinate(lat_value, field="latitude", minimum=-90, maximum=90)
        longitude = normalize_coordinate(lon_value, field="longitude", minimum=-180, maximum=180)
        radius = normalize_radius(radius_value, default=NEARBY_SELLERS_DEFAULT_RADIUS_KM, minimum=NEARBY_SELLERS_MIN_RADIUS_KM, maximum=NEARBY_SELLERS_MAX_RADIUS_KM)
        return {"latitude": latitude, "longitude": longitude, "radius_km": radius, **radius_bbox(latitude=latitude, longitude=longitude, radius_km=radius)}

    @staticmethod
    def _distance_expression() -> str:
        return f"""({_EARTH_RADIUS_KM} * 2 * ASIN(SQRT(
            POWER(SIN(RADIANS(%s - s.latitude) / 2), 2)
            + COS(RADIANS(%s)) * COS(RADIANS(s.latitude))
            * POWER(SIN(RADIANS(%s - s.longitude) / 2), 2)
        ))) AS distance_km"""
