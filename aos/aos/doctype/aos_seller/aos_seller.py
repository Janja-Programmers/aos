# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.sellers.constants import (
    ABOUT_BUSINESS_MAX_LENGTH,
    BUSINESS_CATEGORY_MAX_LENGTH,
    OPERATING_DAYS,
    SELLER_STATUSES,
    SELLER_TYPES,
    STATUS_ACTIVE,
)
from aos.services.sellers.identity import ensure_public_seller_id, normalize_public_seller_id
from aos.utils.doctype_permissions import has_doctype_permission

LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0
LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0
COUNTRY_CODE_LENGTH = 2
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_SCRIPT_SCHEME_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.IGNORECASE)
_DISALLOWED_INVISIBLE = frozenset({"\u200b", "\u2060", "\ufeff", *[chr(value) for value in range(0x202A, 0x202F)], *[chr(value) for value in range(0x2066, 0x206A)]})

class AOSSeller(Document):
    """Seller aggregate fail-closed persistence boundary."""

    def before_insert(self):
        if not self.status:
            self.status = STATUS_ACTIVE
        if not self.storefront_version:
            self.storefront_version = 0
        if not self.status_changed_at:
            self.status_changed_at = now_datetime()
        if not self.status_reason_code:
            self.status_reason_code = "SELLER_CREATED"
        if not self.status_source:
            self.status_source = "seller_controller"
        ensure_public_seller_id(self)

    def validate(self):
        self._validate_user()
        self._validate_public_id()
        self._validate_lifecycle()
        self._normalize_storefront_text()
        self._validate_storefront_ownership()
        self._validate_operating_hours()
        self._validate_and_normalize_location()
        self._validate_metrics()

    def _is_privileged(self) -> bool:
        user = str(getattr(frappe.session, "user", "") or "").strip()
        return has_doctype_permission(
            user=user,
            doctype=self.doctype,
            ptype="write",
        )

    def _validate_user(self):
        if not self.user:
            frappe.throw(_("User is required."))
        if not frappe.db.exists("User", self.user):
            frappe.throw(_("User does not exist."))
        previous = self.get_doc_before_save()
        if previous and previous.user != self.user:
            frappe.throw(_("Seller ownership cannot be changed."), exc=frappe.PermissionError)

    def _validate_public_id(self):
        if not normalize_public_seller_id(self.public_id):
            ensure_public_seller_id(self)
        previous = self.get_doc_before_save()
        if previous and previous.public_id and previous.public_id != self.public_id:
            frappe.throw(_("Public seller ID cannot be changed."), exc=frappe.PermissionError)

    def _validate_lifecycle(self):
        if self.status not in SELLER_STATUSES:
            frappe.throw(_("Invalid seller status."))
        if self.seller_type not in SELLER_TYPES:
            frappe.throw(_("Invalid seller type."))
        previous = self.get_doc_before_save()
        if not previous:
            return
        if previous.status != self.status:
            trusted = bool(self.flags.get("aos_seller_lifecycle_action")) or self._is_privileged()
            if not trusted:
                frappe.throw(_("Seller status can only be changed through the Seller lifecycle service."), exc=frappe.PermissionError)
            if not self.status_reason_code or not self.status_source:
                frappe.throw(_("Seller status reason and source are required."))
            self.status_changed_at = self.status_changed_at or now_datetime()
        if previous.seller_type != self.seller_type:
            trusted = bool(self.flags.get("aos_seller_trusted_projection")) or self._is_privileged()
            if not trusted:
                frappe.throw(_("Seller type can only be changed by Verification or an administrator."), exc=frappe.PermissionError)

    def _normalize_storefront_text(self):
        self.business_category = _normalize_plain_text(
            self.business_category,
            label=_('Business category'),
            max_length=BUSINESS_CATEGORY_MAX_LENGTH,
            multiline=False,
        )
        self.about_business = _normalize_plain_text(
            self.about_business,
            label=_('About business'),
            max_length=ABOUT_BUSINESS_MAX_LENGTH,
            multiline=True,
        )

    def _validate_storefront_ownership(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        protected = {
            "rating",
            "total_reviews",
            "total_ads",
            "chat_response_time_seconds",
            "chat_response_rate",
            "chat_response_sample_size",
            "chat_response_requests",
            "response_metrics_updated_at",
        }
        if any(previous.get(field) != self.get(field) for field in protected):
            if not (
                self._is_privileged()
                or self.flags.get("aos_metric_update")
                or self.flags.get("aos_review_aggregate_update")
            ):
                frappe.throw(_("Seller metrics are server controlled."), exc=frappe.PermissionError)
        location_fields = {
            "has_location",
            "latitude",
            "longitude",
            "display_address",
            "locality",
            "region",
            "country_code",
            "location_name",
            "location_instructions",
            "location_updated_at",
            "location_version",
        }
        if any(previous.get(field) != self.get(field) for field in location_fields):
            if not (self.flags.get("aos_seller_location_action") or self._is_privileged()):
                frappe.throw(
                    _("Seller location must be changed through the Seller service."),
                    exc=frappe.PermissionError,
                )
        storefront = {"business_category", "about_business", "shop_banner_media", "operating_hours"}
        changed = any(previous.get(field) != self.get(field) for field in storefront - {"operating_hours"})
        if _operating_hours_signature(previous.operating_hours) != _operating_hours_signature(self.operating_hours):
            changed = True
        if changed and not (
            self.flags.get("aos_storefront_update")
            or self.flags.get("aos_seller_trusted_projection")
            or self._is_privileged()
        ):
            frappe.throw(_("Seller storefront must be changed through the Seller service."), exc=frappe.PermissionError)

    def _validate_operating_hours(self):
        if not self.operating_hours:
            return
        seen_days: set[str] = set()
        for row in self.operating_hours:
            day = str(row.day_of_week or "").strip()
            if day not in OPERATING_DAYS or day in seen_days:
                frappe.throw(_("Invalid or duplicate operating-hours day."))
            seen_days.add(day)
            row.day_of_week = day
            if row.is_open:
                if not row.open_time or not row.close_time:
                    frappe.throw(_("Open and close time are required when the business is open."))
                if row.open_time >= row.close_time:
                    frappe.throw(_("Open time must be earlier than close time."))
            else:
                row.open_time = None
                row.close_time = None

    def _validate_and_normalize_location(self):
        self._normalize_location_text_fields()

        # Frappe materializes empty Float fields as ``0.0`` on persisted
        # documents. Coordinates therefore cannot be used to infer whether a
        # seller has deliberately published a map location: doing so makes an
        # unrelated storefront or lifecycle save interpret an empty location
        # as the valid coordinate pair (0, 0). ``has_location`` is the
        # canonical publication flag set by the Seller location service.
        if not _is_checked(self.has_location):
            self._clear_resolved_location()
            return

        has_latitude = _has_value(self.latitude)
        has_longitude = _has_value(self.longitude)
        if has_latitude != has_longitude:
            frappe.throw(_("Latitude and longitude must be provided together."))
        if not has_latitude:
            frappe.throw(_("Latitude and longitude are required when a seller location is set."))
        latitude = _parse_coordinate(self.latitude, label=_("Latitude"))
        longitude = _parse_coordinate(self.longitude, label=_("Longitude"))
        if not LATITUDE_MIN <= latitude <= LATITUDE_MAX:
            frappe.throw(_("Latitude must be between -90 and 90."))
        if not LONGITUDE_MIN <= longitude <= LONGITUDE_MAX:
            frappe.throw(_("Longitude must be between -180 and 180."))
        if not self.display_address:
            frappe.throw(_("Display address is required when a seller location is set."))
        if not self.country_code or len(self.country_code) != COUNTRY_CODE_LENGTH or not self.country_code.isalpha():
            frappe.throw(_("Country code must be a 2-character ISO country code."))
        self.latitude = latitude
        self.longitude = longitude
        self.country_code = self.country_code.upper()
        self.has_location = 1

    def _validate_metrics(self):
        for field in (
            "total_ads",
            "total_reviews",
            "chat_response_time_seconds",
            "chat_response_sample_size",
            "chat_response_requests",
            "storefront_version",
            "location_version",
        ):
            value = int(self.get(field) or 0)
            if value < 0:
                frappe.throw(_("Seller counters cannot be negative."))
            self.set(field, value)
        rating = float(self.rating or 0)
        response_rate = float(self.chat_response_rate or 0)
        if not math.isfinite(rating) or rating < 0 or rating > 5:
            frappe.throw(_("Seller rating must be between 0 and 5."))
        if not math.isfinite(response_rate) or response_rate < 0 or response_rate > 100:
            frappe.throw(_("Seller response rate must be between 0 and 100."))
        self.rating = round(rating, 2)
        self.chat_response_rate = round(response_rate, 2)

    def _normalize_location_text_fields(self):
        self.location_name = _normalize_optional_string(self.location_name)
        self.location_instructions = _normalize_optional_string(self.location_instructions)
        self.locality = _normalize_optional_string(self.locality)
        self.region = _normalize_optional_string(self.region)
        self.country_code = _normalize_optional_string(self.country_code)
        self.display_address = _normalize_optional_string(self.display_address)

    def _clear_resolved_location(self):
        self.has_location = 0
        self.latitude = None
        self.longitude = None
        self.location_name = None
        self.location_instructions = None
        self.locality = None
        self.region = None
        self.country_code = None
        self.display_address = None
        self.location_updated_at = None


def _normalize_plain_text(value: Any, *, label: str, max_length: int, multiline: bool) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list, tuple, set, bool)):
        frappe.throw(_("{0} is invalid.").format(label))
    text = unicodedata.normalize("NFC", str(value))
    if "\x00" in text or any(
        (unicodedata.category(char) == "Cc" and char not in {"\n", "\r", "\t"})
        or char in _DISALLOWED_INVISIBLE
        for char in text
    ):
        frappe.throw(_("{0} is invalid.").format(label))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text) if multiline else re.sub(r"\s+", " ", text)
    if len(text) > max_length:
        frappe.throw(_("{0} cannot exceed {1} characters.").format(label, max_length))
    if _HTML_TAG_RE.search(text) or _SCRIPT_SCHEME_RE.search(text):
        frappe.throw(_("{0} must be plain text.").format(label))
    return text or None


def _is_checked(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError, OverflowError):
        return False


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _parse_coordinate(value: Any, *, label: str) -> float:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        frappe.throw(_("{0} must be a valid number.").format(label))
    if not math.isfinite(coordinate):
        frappe.throw(_("{0} must be a finite number.").format(label))
    return round(coordinate, 7)



def _operating_hours_signature(rows: Any) -> tuple[tuple[str, bool, str, str], ...]:
    """Compare child rows by persisted business values, never object identity."""

    signature: list[tuple[str, bool, str, str]] = []
    for row in rows or []:
        getter = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
        is_open = bool(getter("is_open", False))
        signature.append(
            (
                str(getter("day_of_week", "") or "").strip(),
                is_open,
                str(getter("open_time", "") or "") if is_open else "",
                str(getter("close_time", "") or "") if is_open else "",
            )
        )
    return tuple(signature)

def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
