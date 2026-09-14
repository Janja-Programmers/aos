"""Core Seller application service.

Discovery and location persistence are deliberately split into dedicated
modules. This service owns Seller status/detail reads and storefront mutation.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.media.media_service import MediaService
from aos.services.social.capabilities import SocialCapabilityService

from .constants import GET_ALLOWED_FIELDS, STATUS_ACTIVE, STATUS_ALLOWED_FIELDS, UPDATE_ALLOWED_FIELDS
from .errors import SellerConflictError, SellerNotFoundError, SellerValidationError
from .identity import normalize_public_seller_id, require_public_seller_id, resolve_public_seller_id
from .observability import seller_log
from .policy import get_seller_for_user, require_storefront_update_allowed
from .repository import get_verification_for_user
from .serializers import serialize_operating_hours, serialize_public_detail, serialize_status
from .validation import (
    ensure_known_fields,
    normalize_about_business,
    normalize_business_category,
    normalize_clear_banner,
    normalize_expected_version,
    normalize_identifier,
    normalize_operating_hours,
)

_DETAIL_FIELDS = [
    "name", "public_id", "user", "status", "seller_type", "business_category",
    "shop_banner_media", "about_business", "creation", "modified",
    "storefront_version", "storefront_updated_at", "has_location", "location_name",
    "location_instructions", "latitude", "longitude", "display_address", "locality",
    "region", "country_code", "location_updated_at", "total_ads", "rating", "total_reviews",
]


class SellerService:
    def get_status(self, *, user: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, STATUS_ALLOWED_FIELDS)
        row = get_seller_for_user(user)
        verification = get_verification_for_user(user)
        result = serialize_status(row, verification=verification)
        seller_log(
            "seller.status.checked",
            seller_id=getattr(row, "name", None) if row else None,
            status=getattr(row, "status", None) if row else None,
            operation="get_status",
        )
        return result

    def get_public(self, *, payload: dict[str, Any], viewer: str | None) -> dict[str, Any]:
        request = dict(payload or {})
        ensure_known_fields(request, GET_ALLOWED_FIELDS)
        public_id = require_public_seller_id(request.get("seller_id"))
        seller_name = resolve_public_seller_id(public_id)
        if not seller_name:
            raise SellerNotFoundError("Seller not found.")

        row = frappe.db.get_value(
            "AOS Seller",
            {"name": seller_name, "status": STATUS_ACTIVE},
            _DETAIL_FIELDS,
            as_dict=True,
        )
        if not row:
            raise SellerNotFoundError("Seller not found.")
        profile = frappe.db.get_value(
            "AOS Profile",
            {"user": row.user},
            ["user", "account_status", "total_followers", "total_following"],
            as_dict=True,
        )
        user_enabled = frappe.db.get_value("User", row.user, "enabled")
        if not profile or not int(user_enabled or 0) or str(profile.account_status or "Active") != "Active":
            raise SellerNotFoundError("Seller not found.")

        relationship = self._detail_relationship(viewer=viewer, target_user=row.user)
        if relationship.get("is_blocked"):
            raise SellerNotFoundError("Seller not found.")
        operating_hours = frappe.get_all(
            "AOS Seller Operating Hours",
            filters={"parenttype": "AOS Seller", "parent": row.name},
            fields=["day_of_week", "is_open", "open_time", "close_time", "idx"],
            order_by="idx asc",
            limit=7,
        )
        verification = get_verification_for_user(row.user)
        row.total_friends = self._friend_count(row.user)
        result = serialize_public_detail(
            row,
            profile=profile,
            verification=verification,
            operating_hours=operating_hours,
            relationship=relationship,
        )
        seller_log("seller.fetched", seller_id=row.name, status=STATUS_ACTIVE, operation="get_public")
        return result

    def update_storefront(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        banner_id = ""
        if request.get("shop_banner_media_id") not in (None, ""):
            banner_id = normalize_identifier(
                request.get("shop_banner_media_id"), field="shop_banner_media_id", max_length=140
            )
        clear_banner = normalize_clear_banner(request.get("clear_shop_banner"))
        if banner_id and clear_banner:
            raise SellerValidationError(
                "Seller banner cannot be set and cleared together.", code="INVALID_SELLER_BANNER"
            )

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
            require_storefront_update_allowed(user)
        current_version = max(0, int(getattr(doc, "storefront_version", 0) or 0))

        proposed_hours = None
        if "operating_hours" in normalized:
            proposed_hours = [
                {
                    "day_of_week": row["day_of_week"],
                    "is_open": bool(row["is_open"]),
                    "open_time": row["open_time"] if row["is_open"] else None,
                    "close_time": row["close_time"] if row["is_open"] else None,
                }
                for row in normalized["operating_hours"]
            ]
        previous_media = str(getattr(doc, "shop_banner_media", "") or "").strip()
        would_change = (
            ("business_category" in normalized and str(doc.business_category or "") != normalized["business_category"])
            or ("about_business" in normalized and str(doc.about_business or "") != normalized["about_business"])
            or (proposed_hours is not None and serialize_operating_hours(list(doc.operating_hours or [])) != proposed_hours)
            or (bool(banner_id) and banner_id != previous_media)
            or (clear_banner and bool(previous_media))
        )
        # Exact idempotent retries succeed even with a stale expected version.
        if would_change and expected_version is not None and expected_version != current_version:
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
        if proposed_hours is not None and serialize_operating_hours(list(doc.operating_hours or [])) != proposed_hours:
            doc.set("operating_hours", normalized["operating_hours"])
            changed = True

        media_service = MediaService()
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
            changed = True
        elif clear_banner and previous_media:
            media_service.release_media(
                media_id=previous_media,
                user=user,
                attached_doctype="AOS Seller",
                attached_name=doc.name,
            )
            doc.shop_banner_media = ""
            changed = True

        if changed:
            doc.storefront_version = current_version + 1
            doc.storefront_updated_at = now_datetime()
            doc.flags.aos_storefront_update = True
            doc.save(ignore_permissions=True)

        media_url = None
        if doc.shop_banner_media:
            try:
                media_url = media_service.get_public_url(doc.shop_banner_media) or None
            except Exception:
                media_url = None
        result = {
            "seller_id": require_public_seller_id(getattr(doc, "public_id", "")),
            "business_category": doc.business_category or None,
            "about_business": doc.about_business or None,
            "shop_banner_media_id": doc.shop_banner_media or None,
            "shop_banner_url": media_url,
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
    def _detail_relationship(*, viewer: str | None, target_user: str) -> dict[str, Any]:
        if not viewer or viewer == "Guest":
            from aos.api.shared.user_display import get_user_display
            from .serializers import guest_relationship

            return guest_relationship(target_user=get_user_display(target_user).get("account_id"))
        return SocialCapabilityService().relationship_projection(viewer=viewer, target=target_user)

    @staticmethod
    def _friend_count(user: str) -> int:
        rows = frappe.db.sql(
            """
            SELECT COUNT(*) AS total_friends
            FROM `tabAOS Follow` outgoing
            INNER JOIN `tabAOS Follow` incoming
              ON incoming.follower_user = outgoing.following_user
             AND incoming.following_user = outgoing.follower_user
            WHERE outgoing.follower_user = %s
            """,
            (user,),
            as_dict=True,
        )
        return max(0, int(rows[0].total_friends or 0)) if rows else 0
