"""
Set My Seller Location.

Creates or replaces the authenticated seller's public map location.

Trusted client input:
- latitude
- longitude
- location_name
- location_instructions

Resolved server-side through Nominatim:
- display_address
- locality
- region
- country_code
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.maps.clients.nominatim_client import (
    NominatimClientError,
    get_nominatim_client,
)
from aos.api.maps.constants import (
    SUPPORTED_COUNTRY_CODE,
)
from aos.api.maps.serializers import (
    serialize_reverse_geocode_result,
)
from aos.api.maps.validators import (
    validate_latitude,
    validate_longitude,
    validate_supported_location,
)
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import (
    SET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
)
from .serializers import (
    serialize_seller_location,
)
from .validators import (
    validate_location_instructions,
    validate_location_name,
)


def set_my_seller_location_impl(**kwargs):
    """
    Set or replace the authenticated seller's public location.

    Client-supplied address metadata is intentionally ignored. The backend
    reverse-geocodes the submitted coordinates and stores the resolved values.
    """

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            "aos:sellers:set_location:"
            f"user:{current_user}"
        ),
        ttl_seconds=60,
        limit=(
            SET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER
        ),
        message=(
            "Too many location updates. "
            "Please try again shortly."
        ),
    )
    if rl:
        return rl

    try:
        seller_name = frappe.db.get_value(
            "AOS Seller",
            {
                "user": current_user,
            },
            "name",
        )

        if not seller_name:
            return fail(
                "Seller profile not found.",
                code="NOT_FOUND",
            )

        seller_doc = frappe.get_doc(
            "AOS Seller",
            seller_name,
        )

        if seller_doc.status != "Active":
            return fail(
                "Seller profile is not available.",
                code="VALIDATION_ERROR",
            )

        latitude = validate_latitude(
            kwargs.get("latitude")
        )

        longitude = validate_longitude(
            kwargs.get("longitude")
        )

        validate_supported_location(
            latitude=latitude,
            longitude=longitude,
            label="Seller location",
        )

        location_name = validate_location_name(
            kwargs.get("location_name")
        )

        location_instructions = (
            validate_location_instructions(
                kwargs.get(
                    "location_instructions"
                )
            )
        )

        resolved_location = _resolve_location(
            latitude=latitude,
            longitude=longitude,
        )

        seller_doc.latitude = latitude
        seller_doc.longitude = longitude

        seller_doc.location_name = (
            location_name
        )

        seller_doc.location_instructions = (
            location_instructions
        )

        seller_doc.display_address = (
            resolved_location[
                "display_address"
            ]
        )

        seller_doc.locality = (
            resolved_location.get(
                "locality"
            )
        )

        seller_doc.region = (
            resolved_location.get(
                "region"
            )
        )

        seller_doc.country_code = (
            resolved_location[
                "country_code"
            ]
        )

        seller_doc.has_location = 1
        seller_doc.location_updated_at = (
            now_datetime()
        )

        seller_doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return ok(
            "Seller location saved successfully.",
            data={
                "seller": seller_doc.name,
                "location": (
                    serialize_seller_location(
                        seller_doc
                    )
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except NominatimClientError as ex:
        frappe.db.rollback()

        return fail(
            str(ex),
            code="MAP_SERVICE_ERROR",
        )

    except Exception:
        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Set Seller Location Failed",
        )

        return fail(
            "Failed to save seller location.",
            code="INTERNAL_ERROR",
        )


def _resolve_location(
    *,
    latitude: float,
    longitude: float,
) -> dict:
    """
    Reverse-geocode coordinates and validate the resolved result.

    The resolved address becomes the backend source of truth, preventing
    clients from submitting false address, region, or country metadata.
    """

    client = get_nominatim_client()

    try:
        raw_location = client.reverse_geocode(
            latitude=latitude,
            longitude=longitude,
            language="en",
        )
    finally:
        client.close()

    location = (
        serialize_reverse_geocode_result(
            raw_location
        )
    )

    display_address = location.get(
        "display_address"
    )

    if not display_address:
        raise NominatimClientError(
            "No usable address was found for this location."
        )

    resolved_latitude = location.get(
        "latitude"
    )

    resolved_longitude = location.get(
        "longitude"
    )

    if (
        resolved_latitude is None
        or resolved_longitude is None
    ):
        raise NominatimClientError(
            "The geocoding service did not return valid coordinates."
        )

    country_code = location.get(
        "country_code"
    )

    if not country_code:
        raise NominatimClientError(
            "The geocoding service did not return a country code."
        )

    normalized_country_code = (
        str(country_code)
        .strip()
        .upper()
    )

    if (
        normalized_country_code
        != SUPPORTED_COUNTRY_CODE
    ):
        raise NominatimClientError(
            "The selected location is outside the supported country."
        )

    return {
        "display_address": display_address,
        "locality": location.get(
            "locality"
        ),
        "region": location.get(
            "region"
        ),
        "country_code": (
            normalized_country_code
        ),
    }
