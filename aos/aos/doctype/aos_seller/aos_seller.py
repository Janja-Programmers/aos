# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import math
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document


LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0

LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0

COUNTRY_CODE_LENGTH = 2


class AOSSeller(Document):
    def validate(self):
        self._validate_user()
        self._validate_operating_hours()
        self._validate_and_normalize_location()

    def _validate_user(self):
        """Ensure the seller belongs to a valid user."""
        if not self.user:
            frappe.throw(_("User is required."))

        if not frappe.db.exists("User", self.user):
            frappe.throw(_("User does not exist."))

    def _validate_operating_hours(self):
        """Validate weekly seller operating hours."""
        if not self.operating_hours:
            return

        seen_days = set()

        for row in self.operating_hours:
            day = row.day_of_week

            if not day:
                frappe.throw(
                    _("Day of week is required in operating hours.")
                )

            if day in seen_days:
                frappe.throw(
                    _(
                        "Duplicate entry for {0} in operating hours."
                    ).format(day)
                )

            seen_days.add(day)

            if row.is_open:
                if not row.open_time or not row.close_time:
                    frappe.throw(
                        _(
                            "{0}: Open and close time are required "
                            "when business is open."
                        ).format(day)
                    )

                if row.open_time >= row.close_time:
                    frappe.throw(
                        _(
                            "{0}: Open time must be earlier than "
                            "close time."
                        ).format(day)
                    )

            else:
                row.open_time = None
                row.close_time = None

    def _validate_and_normalize_location(self):
        """
        Validate and normalize the seller's public map location.

        Rules:
        - Latitude and longitude must either both be supplied or both be empty.
        - Latitude must be between -90 and 90.
        - Longitude must be between -180 and 180.
        - A saved coordinate must have a resolved display address.
        - Country code is normalized to uppercase ISO alpha-2.
        - has_location is derived by the backend.
        - Resolved location fields are cleared when coordinates are removed.
        """
        self._normalize_location_text_fields()

        has_latitude = _has_value(self.latitude)
        has_longitude = _has_value(self.longitude)

        if has_latitude != has_longitude:
            frappe.throw(
                _(
                    "Latitude and longitude must be provided together."
                )
            )

        if not has_latitude:
            self._clear_resolved_location()
            return

        latitude = _parse_coordinate(
            self.latitude,
            label=_("Latitude"),
        )
        longitude = _parse_coordinate(
            self.longitude,
            label=_("Longitude"),
        )

        if not LATITUDE_MIN <= latitude <= LATITUDE_MAX:
            frappe.throw(
                _("Latitude must be between -90 and 90.")
            )

        if not LONGITUDE_MIN <= longitude <= LONGITUDE_MAX:
            frappe.throw(
                _("Longitude must be between -180 and 180.")
            )

        if not self.display_address:
            frappe.throw(
                _(
                    "Display address is required when a seller "
                    "location is set."
                )
            )

        if not self.country_code:
            frappe.throw(
                _(
                    "Country code is required when a seller "
                    "location is set."
                )
            )

        if len(self.country_code) != COUNTRY_CODE_LENGTH:
            frappe.throw(
                _(
                    "Country code must be a 2-character ISO "
                    "country code."
                )
            )

        if not self.country_code.isalpha():
            frappe.throw(
                _("Country code must contain letters only.")
            )

        self.latitude = latitude
        self.longitude = longitude
        self.country_code = self.country_code.upper()
        self.has_location = 1

    def _normalize_location_text_fields(self):
        """Trim seller-entered and geocoder-resolved location text."""
        self.location_name = _normalize_optional_string(
            self.location_name
        )
        self.location_instructions = _normalize_optional_string(
            self.location_instructions
        )
        self.locality = _normalize_optional_string(
            self.locality
        )
        self.region = _normalize_optional_string(
            self.region
        )
        self.country_code = _normalize_optional_string(
            self.country_code
        )
        self.display_address = _normalize_optional_string(
            self.display_address
        )

    def _clear_resolved_location(self):
        """
        Clear map-derived fields when the seller has no coordinates.

        Seller-entered location_name and location_instructions are also
        cleared because they no longer describe an active map location.
        """
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


def _has_value(value: Any) -> bool:
    """
    Return whether a value is present.

    Zero is a valid geographic coordinate and must not be treated as empty.
    """
    return value is not None and str(value).strip() != ""


def _parse_coordinate(
    value: Any,
    *,
    label: str,
) -> float:
    """Parse and validate a finite numeric coordinate."""
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        frappe.throw(
            _("{0} must be a valid number.").format(label)
        )

    if not math.isfinite(coordinate):
        frappe.throw(
            _("{0} must be a finite number.").format(label)
        )

    return round(coordinate, 7)


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty strings to None."""
    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
