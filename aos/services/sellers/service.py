"""Seller application service.

Owns Seller request validation, public discovery/detail reads, storefront
updates, status/capability serialization, Media attachment orchestration, and
viewer-specific relationship projection. API modules remain thin.
"""

from __future__ import annotations

import math
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.maps.validators import clamp_bbox_to_supported_area, is_supported_location
from aos.api.shared.sql_safety import safe_like_contains
from aos.services.social.capabilities import SocialCapabilityService
from aos.services.media.media_service import MediaService
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map as social_relationship_map

from .constants import (
    GET_ALLOWED_FIELDS,
    LIST_ALLOWED_FIELDS,
    STATUS_ACTIVE,
    STATUS_ALLOWED_FIELDS,
    UPDATE_ALLOWED_FIELDS,
)
from .errors import (
    SellerConflictError,
    SellerNotFoundError,
    SellerValidationError,
)
from .identity import (
    migration_fallback_public_seller_id,
    normalize_public_seller_id,
    resolve_seller_reference,
)
from .observability import seller_log
from .policy import get_seller_for_user, require_storefront_update_allowed
from .serializers import (
    display_map,
    guest_relationship,
    serialize_operating_hours,
    serialize_public_detail,
    serialize_public_list_item,
    serialize_status,
)
from .validation import (
    ensure_known_fields,
    normalize_about_business,
    normalize_business_category,
    normalize_clear_banner,
    normalize_coordinate,
    normalize_country_code,
    normalize_expected_version,
    normalize_follow_filter,
    normalize_identifier,
    normalize_location_filter,
    normalize_operating_hours,
    normalize_optional_boolean,
    normalize_pagination,
    normalize_radius,
    normalize_search,
    normalize_seller_type,
    normalize_sort,
)

_EARTH_RADIUS_KM = 6371.0
_DEFAULT_RADIUS_KM = 10.0
_MIN_RADIUS_KM = 0.1
_MAX_RADIUS_KM = 100.0

_SELLER_FIELDS = [
    "name",
    "public_id",
    "user",
    "status",
    "seller_type",
    "business_category",
    "shop_banner",
    "shop_banner_media",
    "about_business",
    "creation",
    "modified",
    "storefront_version",
    "storefront_updated_at",
    "has_location",
    "location_name",
    "location_instructions",
    "latitude",
    "longitude",
    "display_address",
    "locality",
    "region",
    "country_code",
    "location_updated_at",
    "total_ads",
    "rating",
    "total_reviews",
    "chat_response_time_seconds",
    "chat_response_rate",
    "chat_response_sample_size",
    "chat_response_requests",
    "response_metrics_updated_at",
]


class SellerService:
    def get_status(self, *, user: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, STATUS_ALLOWED_FIELDS)
        row = get_seller_for_user(user)
        verified = bool(
            frappe.db.get_value("AOS Profile", {"user": user}, "is_verified") or 0
        )
        result = serialize_status(row, verified=verified)
        seller_log(
            "seller.status.checked",
            seller_id=getattr(row, "name", None) if row else None,
            status=getattr(row, "status", None) if row else None,
            operation="get_status",
        )
        return result

    def get_public(
        self,
        *,
        payload: dict[str, Any],
        viewer: str | None,
    ) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, GET_ALLOWED_FIELDS)
        reference = (
            request.get("seller")
            or request.get("seller_id")
            or request.get("id")
        )
        reference = normalize_identifier(reference, field="seller", max_length=140)
        seller_name = resolve_seller_reference(reference)
        if not seller_name:
            raise SellerNotFoundError("Seller not found.")
        row = frappe.db.get_value(
            "AOS Seller",
            {"name": seller_name, "status": STATUS_ACTIVE},
            _SELLER_FIELDS,
            as_dict=True,
        )
        if not row:
            raise SellerNotFoundError("Seller not found.")
        profile = frappe.db.get_value(
            "AOS Profile",
            {"user": row.user},
            [
                "user",
                "account_status",
                "total_followers",
                "total_following",
                "is_verified",
            ],
            as_dict=True,
        )
        user_enabled = frappe.db.get_value("User", row.user, "enabled")
        if (
            not profile
            or not int(user_enabled or 0)
            or str(profile.account_status or "Active") != "Active"
        ):
            raise SellerNotFoundError("Seller not found.")
        row.total_friends = self._friend_counts([row.user]).get(row.user, 0)
        operating_hours = frappe.get_all(
            "AOS Seller Operating Hours",
            filters={"parenttype": "AOS Seller", "parent": row.name},
            fields=["day_of_week", "is_open", "open_time", "close_time", "idx"],
            order_by="idx asc",
        )
        relationship = self._detail_relationship(viewer=viewer, target_user=row.user)
        if relationship.get("is_blocked"):
            # Direct lookups must not bypass the discovery block boundary.
            raise SellerNotFoundError("Seller not found.")
        result = serialize_public_detail(
            row,
            profile=profile,
            operating_hours=operating_hours,
            relationship=relationship,
        )
        # Ensure partially migrated rows still expose a stable opaque ID.
        public_id = normalize_public_seller_id(row.public_id) or migration_fallback_public_seller_id(row.name)
        result["seller"] = public_id
        result["seller_id"] = public_id
        seller_log("seller.fetched", seller_id=row.name, status=STATUS_ACTIVE, operation="get_public")
        return result

    def list_public(
        self,
        *,
        payload: dict[str, Any],
        viewer: str | None,
    ) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, LIST_ALLOWED_FIELDS)
        authenticated = bool(viewer and viewer != "Guest")
        limit, offset = normalize_pagination(request)
        search = normalize_search(request.get("search") or request.get("q"))
        seller_type = normalize_seller_type(request.get("seller_type"))
        business_category = normalize_business_category(
            request.get("business_category") or request.get("category")
        )
        follow_filter = normalize_follow_filter(
            request.get("follow_filter"),
            authenticated=authenticated,
        )
        locality = normalize_location_filter(request.get("locality"), field="locality")
        region = normalize_location_filter(request.get("region"), field="region")
        country_code = normalize_country_code(request.get("country_code"))
        verified = normalize_optional_boolean(request.get("is_verified"), field="is_verified")
        has_location = normalize_optional_boolean(request.get("has_location"), field="has_location")
        sort = normalize_sort(request.get("sort"))
        geo = self._geo_context(request)
        if sort == "nearest" and not geo:
            raise SellerValidationError(
                "Latitude and longitude are required for nearest sorting.",
                code="INVALID_SELLER_LOCATION",
            )
        if geo and has_location is None:
            has_location = 1

        conditions = [
            "s.status = 'Active'",
            "u.enabled = 1",
            "COALESCE(p.account_status, 'Active') = 'Active'",
        ]
        params: list[Any] = []
        if authenticated:
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
        if verified is not None:
            conditions.append("COALESCE(p.is_verified, 0) = %s")
            params.append(verified)
        if has_location is not None:
            conditions.append("COALESCE(s.has_location, 0) = %s")
            params.append(has_location)
        if seller_type:
            conditions.append("s.seller_type = %s")
            params.append(seller_type)
        if business_category:
            conditions.append("s.business_category = %s")
            params.append(business_category)
        if locality:
            conditions.append("s.locality = %s")
            params.append(locality)
        if region:
            conditions.append("s.region = %s")
            params.append(region)
        if country_code:
            conditions.append("s.country_code = %s")
            params.append(country_code)
        if search:
            escaped = safe_like_contains(search)
            conditions.append(
                """
                (
                    p.display_name LIKE %s ESCAPE '\\\\'
                    OR s.business_category LIKE %s ESCAPE '\\\\'
                    OR s.about_business LIKE %s ESCAPE '\\\\'
                    OR s.location_name LIKE %s ESCAPE '\\\\'
                    OR s.display_address LIKE %s ESCAPE '\\\\'
                    OR s.locality LIKE %s ESCAPE '\\\\'
                    OR s.region LIKE %s ESCAPE '\\\\'
                )
                """
            )
            params.extend([escaped] * 7)
        if follow_filter:
            if follow_filter == "following":
                conditions.append(
                    "EXISTS (SELECT 1 FROM `tabAOS Follow` f WHERE f.follower_user = %s AND f.following_user = s.user)"
                )
            else:
                conditions.append(
                    "NOT EXISTS (SELECT 1 FROM `tabAOS Follow` f WHERE f.follower_user = %s AND f.following_user = s.user)"
                )
            params.append(viewer)

        select_params: list[Any] = []
        having = ""
        having_params: list[Any] = []
        distance_select = "NULL AS distance_km"
        if geo:
            conditions.extend(
                [
                    "COALESCE(s.has_location, 0) = 1",
                    "s.latitude IS NOT NULL",
                    "s.longitude IS NOT NULL",
                    "s.latitude BETWEEN %s AND %s",
                    "s.longitude BETWEEN %s AND %s",
                ]
            )
            params.extend([geo["south"], geo["north"], geo["west"], geo["east"]])
            distance_select = self._distance_expression()
            select_params.extend([geo["latitude"], geo["latitude"], geo["longitude"]])
            having = "HAVING distance_km <= %s"
            having_params.append(geo["radius_km"])

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name, s.public_id, s.user, s.seller_type, s.business_category,
                s.shop_banner, s.shop_banner_media, s.rating, s.total_reviews,
                s.total_ads, s.creation, s.has_location, s.location_name,
                s.locality, s.region, s.country_code, s.latitude, s.longitude,
                s.chat_response_time_seconds, s.chat_response_rate,
                s.chat_response_sample_size, s.chat_response_requests,
                s.response_metrics_updated_at,
                COALESCE(p.total_followers, 0) AS total_followers,
                COALESCE(p.total_following, 0) AS total_following,
                COALESCE(p.is_verified, 0) AS is_verified,
                {distance_select}
            FROM `tabAOS Seller` s
            INNER JOIN `tabUser` u ON u.name = s.user
            INNER JOIN `tabAOS Profile` p ON p.user = s.user
            WHERE {' AND '.join(conditions)}
            {having}
            {self._order_clause(sort=sort, has_geo=bool(geo))}
            LIMIT %s OFFSET %s
            """,
            (*select_params, *params, *having_params, limit + 1, offset),
            as_dict=True,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        users = [str(row.user or "") for row in rows if row.user]
        friend_counts = self._friend_counts(users)
        for row in rows:
            row.total_friends = friend_counts.get(row.user, 0)
        displays = display_map(users)
        relationships = self._relationship_map(viewer=viewer if authenticated else None, targets=users, displays=displays)
        items: list[dict[str, Any]] = []
        for row in rows:
            public_id = normalize_public_seller_id(row.public_id) or migration_fallback_public_seller_id(row.name)
            row.public_id = public_id
            item = serialize_public_list_item(
                row,
                display=displays.get(row.user) or {},
                relationship=relationships.get(row.user) or guest_relationship(
                    target_user=(displays.get(row.user) or {}).get("user")
                ),
            )
            item["seller"] = public_id
            item["seller_id"] = public_id
            items.append(item)
        result: dict[str, Any] = {
            "items": items,
            "limit": limit,
            "offset": offset,
            "count": len(items),
            "has_more": has_more,
            "next_offset": offset + len(items) if has_more else None,
            "sort": sort,
        }
        if geo:
            result["nearby"] = {
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "radius_km": geo["radius_km"],
                "sort": sort,
            }
        seller_log("seller.listed", operation="list_public", count=len(items))
        return result

    def update_storefront(
        self,
        *,
        user: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, UPDATE_ALLOWED_FIELDS)
        if not request:
            raise SellerValidationError("No seller fields were provided.", code="INVALID_SELLER_REQUEST")
        normalized: dict[str, Any] = {}
        if "business_category" in request:
            normalized["business_category"] = normalize_business_category(request.get("business_category"))
        if "about_business" in request:
            normalized["about_business"] = normalize_about_business(request.get("about_business"))
        if "operating_hours" in request:
            normalized["operating_hours"] = normalize_operating_hours(request.get("operating_hours"))
        expected_version = normalize_expected_version(request.get("expected_version"))
        banner_id, clear_banner = self._banner_request(request)

        initial = require_storefront_update_allowed(user)
        locked = frappe.db.sql(
            "SELECT name FROM `tabAOS Seller` WHERE name = %s AND user = %s FOR UPDATE",
            (initial.name, user),
            as_dict=True,
        )
        if not locked:
            raise SellerNotFoundError("Seller profile not found.")
        doc = frappe.get_doc("AOS Seller", initial.name)
        if str(doc.status or "") != STATUS_ACTIVE:
            # State may have changed after the pre-check but before the lock.
            require_storefront_update_allowed(user)
        current_version = max(0, int(getattr(doc, "storefront_version", 0) or 0))
        if expected_version is not None and expected_version != current_version:
            raise SellerConflictError(
                "Seller storefront has changed. Refresh and try again.",
                data={"current_version": current_version},
            )

        changed = False
        if "business_category" in normalized and str(doc.business_category or "") != normalized["business_category"]:
            doc.business_category = normalized["business_category"] or None
            changed = True
        if "about_business" in normalized and str(doc.about_business or "") != normalized["about_business"]:
            doc.about_business = normalized["about_business"] or None
            changed = True
        if "operating_hours" in normalized:
            existing_hours = serialize_operating_hours(list(doc.operating_hours or []))
            if existing_hours != [
                {
                    "day_of_week": row["day_of_week"],
                    "is_open": bool(row["is_open"]),
                    "open_time": row["open_time"] if row["is_open"] else None,
                    "close_time": row["close_time"] if row["is_open"] else None,
                }
                for row in normalized["operating_hours"]
            ]:
                doc.set("operating_hours", normalized["operating_hours"])
                changed = True

        media_service = MediaService()
        previous_media = str(getattr(doc, "shop_banner_media", "") or "").strip()
        if banner_id and banner_id != previous_media:
            media = media_service.attach_media(
                media_id=banner_id,
                user=user,
                purpose="seller_banner",
                attached_doctype="AOS Seller",
                attached_name=doc.name,
                attached_field="shop_banner_media",
                replacing_media_id=previous_media or None,
            )
            if previous_media:
                media_service.release_media(
                    media_id=previous_media,
                    user=user,
                    attached_doctype="AOS Seller",
                    attached_name=doc.name,
                    replacement_media_id=banner_id,
                )
            doc.shop_banner_media = media.name
            doc.shop_banner = media_service.get_public_url(media.name) or ""
            changed = True
        elif clear_banner and previous_media:
            media_service.release_media(
                media_id=previous_media,
                user=user,
                attached_doctype="AOS Seller",
                attached_name=doc.name,
            )
            doc.shop_banner_media = ""
            doc.shop_banner = ""
            changed = True
        elif clear_banner and str(doc.shop_banner or ""):
            doc.shop_banner = ""
            changed = True

        if changed:
            doc.storefront_version = current_version + 1
            doc.storefront_updated_at = now_datetime()
            doc.flags.aos_storefront_update = True
            doc.save(ignore_permissions=True)
        result = {
            "seller_id": normalize_public_seller_id(getattr(doc, "public_id", ""))
            or migration_fallback_public_seller_id(doc.name),
            "business_category": doc.business_category or None,
            "about_business": doc.about_business or None,
            "shop_banner": self._safe_banner_url(doc),
            "shop_banner_media": getattr(doc, "shop_banner_media", None) or None,
            "shop_banner_media_id": getattr(doc, "shop_banner_media", None) or None,
            "seller_type": doc.seller_type,
            "operating_hours": serialize_operating_hours(list(doc.operating_hours or [])),
            "storefront_version": max(0, int(getattr(doc, "storefront_version", 0) or 0)),
            "storefront_updated_at": getattr(doc, "storefront_updated_at", None),
            "changed": changed,
        }
        seller_log(
            "seller.storefront.updated",
            seller_id=doc.name,
            status=doc.status,
            operation="update",
            outcome="success" if changed else "idempotent",
        )
        return result

    @staticmethod
    def _safe_banner_url(doc: Any) -> str | None:
        media_id = str(getattr(doc, "shop_banner_media", "") or "").strip()
        if media_id:
            try:
                return MediaService().get_public_url(media_id) or None
            except Exception:
                return None
        legacy = str(getattr(doc, "shop_banner", "") or "").strip()
        return legacy if legacy.startswith("https://") else None

    @staticmethod
    def _banner_request(request: dict[str, Any]) -> tuple[str, bool]:
        candidates: list[str] = []
        for key in ("shop_banner_media", "banner_media", "media_id"):
            value = request.get(key)
            if value not in (None, ""):
                candidates.append(normalize_identifier(value, field="seller_banner", max_length=140))
        legacy = request.get("shop_banner")
        if legacy not in (None, ""):
            legacy_value = normalize_identifier(legacy, field="seller_banner", max_length=140)
            if not legacy_value.startswith("MEDIA-"):
                raise SellerValidationError(
                    "Shop banner must be uploaded using Media.",
                    code="INVALID_SELLER_BANNER",
                )
            candidates.append(legacy_value)
        unique = list(dict.fromkeys(candidates))
        if len(unique) > 1:
            raise SellerValidationError("Conflicting seller banner values.", code="INVALID_SELLER_BANNER")
        clear = normalize_clear_banner(request.get("clear_shop_banner"))
        if "shop_banner" in request and request.get("shop_banner") in (None, ""):
            clear = True
        if unique and clear:
            raise SellerValidationError("Seller banner cannot be set and cleared together.", code="INVALID_SELLER_BANNER")
        return (unique[0] if unique else ""), clear

    @staticmethod
    def _detail_relationship(*, viewer: str | None, target_user: str) -> dict[str, Any]:
        if not viewer or viewer == "Guest":
            from aos.api.shared.user_display import get_user_display

            return guest_relationship(target_user=get_user_display(target_user).get("account_id"))
        return SocialCapabilityService().relationship_projection(viewer=viewer, target=target_user)

    @staticmethod
    def _friend_counts(users: list[str]) -> dict[str, int]:
        unique = sorted({str(user).strip() for user in users if user})
        if not unique:
            return {}
        rows = frappe.db.sql(
            """
            SELECT outgoing.follower_user AS user, COUNT(*) AS total_friends
            FROM `tabAOS Follow` outgoing
            INNER JOIN `tabAOS Follow` incoming
              ON incoming.follower_user = outgoing.following_user
             AND incoming.following_user = outgoing.follower_user
            WHERE outgoing.follower_user IN %(users)s
            GROUP BY outgoing.follower_user
            """,
            {"users": tuple(unique)},
            as_dict=True,
        )
        return {str(row.user): max(0, int(row.total_friends or 0)) for row in rows}

    @staticmethod
    def _relationship_map(
        *,
        viewer: str | None,
        targets: list[str],
        displays: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        unique = sorted({str(target).strip() for target in targets if target})
        if not viewer or viewer == "Guest" or not unique:
            return {
                target: guest_relationship(target_user=(displays.get(target) or {}).get("user"))
                for target in unique
            }
        return social_relationship_map(
            repository=SocialRepository(),
            viewer=viewer,
            targets=unique,
        )

    @staticmethod
    def _geo_context(request: dict[str, Any]) -> dict[str, float] | None:
        latitude_value = request.get("latitude") if "latitude" in request else request.get("lat")
        longitude_value = (
            request.get("longitude")
            if "longitude" in request
            else request.get("lon")
            if "lon" in request
            else request.get("lng")
        )
        radius_value = request.get("radius_km")
        has_lat = latitude_value not in (None, "")
        has_lon = longitude_value not in (None, "")
        has_radius = radius_value not in (None, "")
        if not has_lat and not has_lon and not has_radius:
            return None
        if not has_lat or not has_lon:
            raise SellerValidationError(
                "Latitude and longitude are required for nearby seller discovery.",
                code="INVALID_SELLER_LOCATION",
            )
        latitude = normalize_coordinate(latitude_value, field="latitude", minimum=-90, maximum=90)
        longitude = normalize_coordinate(longitude_value, field="longitude", minimum=-180, maximum=180)
        if not is_supported_location(latitude=latitude, longitude=longitude):
            raise SellerValidationError(
                "Location is outside the supported AOS Maps coverage area.",
                code="INVALID_SELLER_LOCATION",
            )
        radius = normalize_radius(
            radius_value,
            default=_DEFAULT_RADIUS_KM,
            minimum=_MIN_RADIUS_KM,
            maximum=_MAX_RADIUS_KM,
        )
        lat_delta = radius / 111.32
        cosine = math.cos(math.radians(latitude))
        lon_delta = 180.0 if abs(cosine) < 0.000001 else radius / (111.32 * cosine)
        clipped = clamp_bbox_to_supported_area(
            north=latitude + lat_delta,
            south=latitude - lat_delta,
            east=longitude + lon_delta,
            west=longitude - lon_delta,
        )
        if not clipped:
            raise SellerValidationError(
                "Nearby seller search area is outside the supported AOS Maps coverage area.",
                code="INVALID_SELLER_LOCATION",
            )
        return {
            "latitude": latitude,
            "longitude": longitude,
            "radius_km": radius,
            **clipped,
        }

    @staticmethod
    def _distance_expression() -> str:
        return f"""
        ({_EARTH_RADIUS_KM} * 2 * ASIN(SQRT(
            POWER(SIN(RADIANS(%s - s.latitude) / 2), 2)
            + COS(RADIANS(%s)) * COS(RADIANS(s.latitude))
            * POWER(SIN(RADIANS(%s - s.longitude) / 2), 2)
        ))) AS distance_km
        """

    @staticmethod
    def _order_clause(*, sort: str, has_geo: bool) -> str:
        if sort == "nearest" and has_geo:
            return "ORDER BY distance_km ASC, COALESCE(p.is_verified, 0) DESC, COALESCE(s.rating, 0) DESC, s.name ASC"
        if sort == "rating":
            return "ORDER BY COALESCE(s.rating, 0) DESC, COALESCE(s.total_reviews, 0) DESC, s.name ASC"
        if sort == "newest":
            return "ORDER BY s.creation DESC, s.name DESC"
        if sort == "most_ads":
            return "ORDER BY COALESCE(s.total_ads, 0) DESC, COALESCE(s.rating, 0) DESC, s.name ASC"
        if sort == "most_reviewed":
            return "ORDER BY COALESCE(s.total_reviews, 0) DESC, COALESCE(s.rating, 0) DESC, s.name ASC"
        return "ORDER BY COALESCE(p.is_verified, 0) DESC, COALESCE(s.rating, 0) DESC, COALESCE(p.total_followers, 0) DESC, s.creation DESC, s.name ASC"
