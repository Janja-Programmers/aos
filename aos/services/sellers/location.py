"""Seller-owned location persistence using canonical Maps primitives."""

from __future__ import annotations

import math
from typing import Any

from frappe.utils import now_datetime

from .constants import STATUS_ACTIVE
from .errors import SellerConflictError, SellerNotFoundError, SellerStateError, SellerValidationError
from .repository import get_location_for_user, get_public_location, lock_by_user
from .serializers import serialize_location_response
from .validation import normalize_country_code, normalize_optional_text

_LOCATION_CLEAR_FIELDS = (
    "latitude", "longitude", "display_address", "locality", "region",
    "country_code", "location_name", "location_instructions", "location_updated_at",
)


class SellerLocationService:
    def get_location(self, *, user: str | None, seller_id: str | None, viewer: str | None) -> dict[str, Any]:
        if seller_id:
            seller = get_public_location(seller_id, viewer=viewer)
            owner = bool(seller and user and seller.get("user") == user)
        else:
            seller = get_location_for_user(str(user or ""))
            owner = True
        if not seller:
            raise SellerNotFoundError("Seller not found.")
        return serialize_location_response(seller, owner=owner)

    def location_input_identical(self, seller: Any, request: dict[str, Any]) -> bool:
        return bool(seller.get("has_location")) and all(
            self._same_value(seller.get(field), request.get(field))
            for field in ("latitude", "longitude", "location_name", "location_instructions")
        )

    def needs_resolution(self, *, user: str, request: dict[str, Any]) -> bool:
        seller = get_location_for_user(user)
        if not seller:
            raise SellerNotFoundError("Seller profile not found.", code="SELLER_REQUIRED", http_status=403)
        if str(seller.get("status") or "").strip() != STATUS_ACTIVE:
            raise SellerStateError(
                "Seller account is not active.", code="SELLER_INACTIVE", http_status=403,
                data={"status": str(seller.get("status") or "").strip() or None},
            )
        return not self.location_input_identical(seller, request)

    def set_location(
        self,
        *,
        user: str,
        request: dict[str, Any],
        resolved_location: dict[str, Any] | None,
    ) -> dict[str, Any]:
        seller = lock_by_user(user)
        if not seller:
            raise SellerNotFoundError("Seller profile not found.", code="SELLER_REQUIRED", http_status=403)
        if str(seller.status or "").strip() != STATUS_ACTIVE:
            raise SellerStateError(
                "Seller account is not active.", code="SELLER_INACTIVE", http_status=403,
                data={"status": str(seller.status or "").strip() or None},
            )
        current_version = max(0, int(seller.location_version or 0))
        if self.location_input_identical(seller, request):
            return serialize_location_response(seller, owner=True, changed=False)
        expected = request.get("expected_version")
        if expected is not None and expected != current_version:
            raise SellerConflictError(
                "Seller location has changed. Refresh and try again.",
                code="SELLER_LOCATION_VERSION_CONFLICT",
                data={"current_version": current_version},
            )
        if not resolved_location:
            raise SellerConflictError(
                "Seller location changed while the request was being prepared. Refresh and try again.",
                code="SELLER_LOCATION_VERSION_CONFLICT",
                data={"current_version": current_version},
            )
        normalized = self._normalize_maps_snapshot(resolved_location)
        seller.latitude = request["latitude"]
        seller.longitude = request["longitude"]
        seller.location_name = request.get("location_name") or None
        seller.location_instructions = request.get("location_instructions") or None
        seller.display_address = normalized["display_address"]
        seller.locality = normalized["locality"]
        seller.region = normalized["region"]
        seller.country_code = normalized["country_code"]
        seller.has_location = 1
        seller.location_updated_at = now_datetime()
        seller.location_version = current_version + 1
        seller.flags.aos_seller_location_action = True
        seller.save(ignore_permissions=True)
        return serialize_location_response(seller, owner=True, changed=True)

    def remove_location(self, *, user: str, request: dict[str, Any]) -> dict[str, Any]:
        seller = lock_by_user(user)
        if not seller:
            raise SellerNotFoundError("Seller profile not found.", code="SELLER_REQUIRED", http_status=403)
        if str(seller.status or "").strip() != STATUS_ACTIVE:
            raise SellerStateError(
                "Seller account is not active.", code="SELLER_INACTIVE", http_status=403,
                data={"status": str(seller.status or "").strip() or None},
            )
        current_version = max(0, int(seller.location_version or 0))
        if not bool(seller.has_location):
            return serialize_location_response(seller, owner=True, changed=False)
        expected = request.get("expected_version")
        if expected is not None and expected != current_version:
            raise SellerConflictError(
                "Seller location has changed. Refresh and try again.",
                code="SELLER_LOCATION_VERSION_CONFLICT",
                data={"current_version": current_version},
            )
        for field in _LOCATION_CLEAR_FIELDS:
            seller.set(field, None)
        seller.has_location = 0
        seller.location_version = current_version + 1
        seller.flags.aos_seller_location_action = True
        seller.save(ignore_permissions=True)
        return serialize_location_response(seller, owner=True, changed=True)

    @staticmethod
    def _normalize_maps_snapshot(resolved: dict[str, Any]) -> dict[str, str | None]:
        display_address = normalize_optional_text(
            resolved.get("display_address"), field="display_address", max_length=500, multiline=False
        )
        country_code = normalize_country_code(resolved.get("country_code"))
        if not display_address or not country_code:
            raise SellerValidationError(
                "Maps could not resolve a complete seller address.", code="SELLER_LOCATION_UNRESOLVED"
            )
        locality = normalize_optional_text(resolved.get("locality"), field="locality", max_length=140, multiline=False)
        region = normalize_optional_text(resolved.get("region"), field="region", max_length=140, multiline=False)
        return {
            "display_address": display_address,
            "locality": locality or None,
            "region": region or None,
            "country_code": country_code,
        }

    @staticmethod
    def _same_value(left: Any, right: Any) -> bool:
        if isinstance(right, float):
            try:
                return math.isclose(float(left), right, rel_tol=0, abs_tol=0.0000001)
            except (TypeError, ValueError, OverflowError):
                return False
        return (str(left).strip() if left is not None else None) == (
            str(right).strip() if right is not None else None
        )
