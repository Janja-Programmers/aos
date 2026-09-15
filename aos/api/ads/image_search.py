"""Canonical public Ads image-search endpoint.

The external embedding/Qdrant service returns bounded candidate public Ad IDs.
Frappe always rechecks canonical Ads eligibility, projects through Media/FX,
and applies geographic reranking last. Vector scores and internal Media matches
are never exposed as public listing state.
"""
from __future__ import annotations

from typing import Any

import frappe

from aos.api.ads.constants import SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP
from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.image_search_client import (
    ImageSearchServiceError,
    ImageSearchUnavailableError,
    ImageSearchValidationError,
    search_by_image_file,
)
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import ensure_known_fields, normalize_identifier, normalize_int
from aos.services.marketplace_discovery.projection import load_public_ad_items

_ALLOWED_FIELDS = frozenset({"limit", "country", "currency", "location"})
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _request_ip() -> str:
    return _norm(getattr(frappe.local, "request_ip", None)) or "unknown"


def _get_uploaded_image() -> Any | None:
    try:
        return frappe.request.files.get("image")
    except Exception:
        return None


def _request_value(kwargs: dict[str, Any], field: str) -> Any:
    if field in kwargs:
        return kwargs.get(field)
    try:
        return getattr(frappe.local, "form_dict", {}).get(field)
    except Exception:
        return None


def _filename(image_file: Any) -> str:
    return _norm(getattr(image_file, "filename", None) or getattr(image_file, "name", None))


def _content_type(image_file: Any) -> str:
    return _norm(getattr(image_file, "content_type", None)).lower()


def _validate_uploaded_image(image_file: Any):
    if not image_file:
        return fail("Image file is required.", error="VALIDATION_ERROR")
    filename = _filename(image_file).lower()
    content_type = _content_type(image_file)
    if content_type and content_type.startswith("image/"):
        return None
    if filename and any(filename.endswith(ext) for ext in _ALLOWED_IMAGE_EXTENSIONS):
        return None
    return fail("Please upload a valid image file.", error="VALIDATION_ERROR", data={"field": "image"})


def _candidate_ids(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        public_id = _norm(row.get("ad_id"))
        if public_id and public_id not in seen:
            seen.add(public_id)
            result.append(public_id)
        if len(result) >= 150:
            break
    return result


def search_ads_by_image_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:ads:image_search:ip:{_request_ip()}",
        ttl_seconds=60,
        limit=SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many search requests. Please try again shortly.",
    )
    if limited:
        return limited

    image_file = _get_uploaded_image()
    validation_error = _validate_uploaded_image(image_file)
    if validation_error:
        return validation_error

    try:
        ensure_known_fields(kwargs, _ALLOWED_FIELDS)
        limit = normalize_int(_request_value(kwargs, "limit"), field="limit", default=20, minimum=1, maximum=50)
        location = normalize_identifier(_request_value(kwargs, "location"), field="location", max_length=140)
        country, currency, market_error = resolve_market_context(
            country=_request_value(kwargs, "country"),
            currency=_request_value(kwargs, "currency"),
        )
        if market_error:
            return market_error

        # Pull bounded headroom because canonical eligibility can remove stale
        # vector candidates before the final geography rerank.
        ai_result = search_by_image_file(image_file=image_file, limit=min(100, max(limit * 3, limit)))
        candidates = _candidate_ids(ai_result.get("items"))
        if not candidates:
            return ok("No matching ads found.", data={"items": []})

        items = load_public_ad_items(
            candidates,
            country=country or "",
            currency=currency or "",
            location=location,
            viewer=current_user(),
            limit=limit,
        )
        return ok("Search successful." if items else "No matching ads found.", data={"items": items})
    except AdsValidationError as exc:
        return fail("Invalid image search request.", error=exc.code, http_status=exc.http_status)
    except ImageSearchValidationError as exc:
        return fail(_norm(exc) or "Invalid image search request.", error="VALIDATION_ERROR")
    except ImageSearchUnavailableError:
        frappe.log_error(frappe.get_traceback(), "Image Search Service Unavailable")
        return fail(
            "Image search is temporarily unavailable. Please try again later.",
            error="IMAGE_SEARCH_UNAVAILABLE",
            http_status=503,
        )
    except ImageSearchServiceError:
        frappe.log_error(frappe.get_traceback(), "Image Search Service Error")
        return fail("Image search failed. Please try again later.", error="IMAGE_SEARCH_FAILED", http_status=502)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Search Ads by Image Failed")
        return fail("Failed to search ads.", error="INTERNAL_ERROR", http_status=500)
