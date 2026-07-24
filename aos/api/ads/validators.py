"""Compatibility adapters for the centralized Ads validator.

New Ads code must import :mod:`aos.services.ads.validation` directly.  These
functions remain only so older internal imports continue to use the same strict
rules instead of maintaining a second, weaker validation implementation.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.responses import fail
from aos.services.ads.constants import (
    MAX_DESCRIPTION_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    MIN_TITLE_LENGTH,
)
from aos.services.ads.errors import AdsValidationError, public_ads_message
from aos.services.ads.validation import (
    normalize_details,
    normalize_images,
    normalize_json_list,
    normalize_text,
)
from aos.services.catalog.errors import CatalogError, public_catalog_message
from aos.services.catalog.service import CatalogService


def normalize_list_payload(value: Any) -> List[Dict[str, Any]]:
    """Return a strict list-of-objects payload for legacy callers."""

    return normalize_json_list(value, field="items", max_items=200)


def validate_basic_fields(title: Any, location: Any, category: Any, description: Any):
    """Validate the legacy four-field creation preflight.

    The tuple response is preserved for compatibility with the previous helper.
    Full mutations still pass through ``normalize_full_ad_payload`` before save.
    """

    try:
        clean_title = normalize_text(
            title,
            field="title",
            max_length=MAX_TITLE_LENGTH,
            min_length=MIN_TITLE_LENGTH,
            required=True,
        )
        clean_location = normalize_text(location, field="location", max_length=140, required=True)
        clean_description = normalize_text(
            description,
            field="description",
            max_length=MAX_DESCRIPTION_LENGTH,
            min_length=MIN_DESCRIPTION_LENGTH,
            required=True,
            multiline=True,
        )
        category_id = CatalogService().assert_sellable_category(category)
    except CatalogError as exc:
        return None, None, None, None, fail(
            public_catalog_message(exc),
            error=exc.code,
            http_status=exc.http_status,
        )
    except AdsValidationError as exc:
        return None, None, None, None, fail(
            public_ads_message(exc, fallback="Invalid ad input."),
            error=exc.code,
            http_status=exc.http_status,
        )

    return clean_title, clean_location, category_id, clean_description, None


def sanitize_details(details: Any, category: str | None = None) -> List[Dict[str, Any]]:
    """Delegate legacy detail normalization to the Catalog-backed validator."""

    if not category:
        frappe.throw("Category is required to validate ad details.", exc=frappe.ValidationError)
    try:
        return normalize_details(details, category=category)
    except (AdsValidationError, CatalogError) as exc:
        frappe.throw(
            (
                public_ads_message(exc, fallback="Invalid category details.")
                if isinstance(exc, AdsValidationError)
                else public_catalog_message(exc)
            ),
            exc=frappe.ValidationError,
        )


def sanitize_images(images: Any) -> List[Dict[str, Any]]:
    """Delegate legacy image normalization to the canonical Media-ID contract."""

    try:
        return normalize_images(images, require_images=False, require_primary=False)
    except AdsValidationError as exc:
        frappe.throw(public_ads_message(exc, fallback="Invalid ad images."), exc=frappe.ValidationError)
