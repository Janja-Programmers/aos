"""
Search Ads by Image (implementation).

Guest accessible.
Rate limited by IP.

AI/ML boundary:
- This API module does not generate embeddings.
- This API module does not talk to Qdrant.
- This API module calls the external image-search service through the
  integration client, then fetches/serializes authoritative ad data from Frappe.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

import frappe

from aos.api.ads.constants import SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP
from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.image_search_client import (
    ImageSearchServiceError,
    ImageSearchUnavailableError,
    ImageSearchValidationError,
    search_by_image_file,
)


_ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any, default: int = 20) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _request_ip() -> str:
    return _norm(getattr(frappe.local, "request_ip", None)) or "unknown"


def _get_uploaded_image() -> Any | None:
    try:
        return frappe.request.files.get("image")
    except Exception:
        return None


def _get_limit(kwargs: Dict[str, Any]) -> int | None:
    raw_limit = (
        kwargs.get("limit")
        or getattr(frappe.local, "form_dict", {}).get("limit")
        or getattr(frappe.local, "form_dict", {}).get("page_length")
    )

    if raw_limit in (None, ""):
        return None

    return _to_int(raw_limit, default=20)


def _filename(image_file: Any) -> str:
    return _norm(
        getattr(image_file, "filename", None)
        or getattr(image_file, "name", None)
    )


def _content_type(image_file: Any) -> str:
    return _norm(getattr(image_file, "content_type", None)).lower()


def _has_allowed_image_extension(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in _ALLOWED_IMAGE_EXTENSIONS)


def _validate_uploaded_image(image_file: Any):
    if not image_file:
        return fail("Image file is required.", error="VALIDATION_ERROR")

    filename = _filename(image_file)
    content_type = _content_type(image_file)

    # Be practical: some mobile clients upload images as application/octet-stream.
    # In that case, allow the file if the filename has a common image extension.
    if content_type and content_type.startswith("image/"):
        return None

    if filename and _has_allowed_image_extension(filename):
        return None

    return fail(
        "Please upload a valid image file.",
        error="VALIDATION_ERROR",
        data={"field": "image"},
    )


def _dedupe_ranked_results(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preserve AI ranking while keeping only the best match per ad."""

    ranked: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for row in items or []:
        if not isinstance(row, dict):
            continue

        ad_id = _norm(row.get("ad_id"))
        if not ad_id or ad_id in seen:
            continue

        seen.add(ad_id)
        ranked.append(
            {
                "ad_id": ad_id,
                "score": row.get("score"),
                "matched_image_url": row.get("matched_image_url"),
                "is_primary": bool(row.get("is_primary") or False),
            }
        )

    return ranked


def _load_active_ad_docs(ad_ids: List[str]) -> Dict[str, Any]:
    if not ad_ids:
        return {}

    rows = frappe.get_all(
        "AOS Ad",
        filters={
            "name": ["in", ad_ids],
            "status": "Active",
        },
        fields=["name"],
        limit_page_length=len(ad_ids),
    )

    docs: Dict[str, Any] = {}

    for row in rows:
        ad_id = _norm(getattr(row, "name", None) or row.get("name"))
        if not ad_id:
            continue

        try:
            docs[ad_id] = frappe.get_doc("AOS Ad", ad_id)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Image Search Result Ad Load Failed: {ad_id}",
            )

    return docs


def _serialize_ranked_ads(
    *,
    ranked_results: List[Dict[str, Any]],
    docs_by_id: Dict[str, Any],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for match in ranked_results:
        ad_id = match["ad_id"]
        ad_doc = docs_by_id.get(ad_id)

        if not ad_doc:
            continue

        item = serialize_ad_list_item(ad_doc)
        item["image_search"] = {
            "score": match.get("score"),
            "matched_image_url": match.get("matched_image_url"),
            "is_primary": bool(match.get("is_primary") or False),
        }
        items.append(item)

    return items


def search_ads_by_image_impl(**kwargs):
    """Search active ads using an uploaded image."""

    rl = rate_limit(
        key=f"aos:ads:image_search:ip:{_request_ip()}",
        ttl_seconds=60,
        limit=SEARCH_BY_IMAGE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many search requests. Please try again shortly.",
    )

    if rl:
        return rl

    image_file = _get_uploaded_image()
    validation_error = _validate_uploaded_image(image_file)

    if validation_error:
        return validation_error

    try:
        ai_result = search_by_image_file(
            image_file=image_file,
            limit=_get_limit(kwargs),
        )

        ranked_results = _dedupe_ranked_results(ai_result.get("items") or [])

        if not ranked_results:
            return ok(
                "No matching ads found.",
                data={"items": []},
            )

        ad_ids = [row["ad_id"] for row in ranked_results]
        docs_by_id = _load_active_ad_docs(ad_ids)

        if not docs_by_id:
            return ok(
                "No matching ads found.",
                data={"items": []},
            )

        results = _serialize_ranked_ads(
            ranked_results=ranked_results,
            docs_by_id=docs_by_id,
        )

        if not results:
            return ok(
                "No matching ads found.",
                data={"items": []},
            )

        return ok(
            ai_result.get("message") or "Search successful.",
            data={"items": results},
        )

    except ImageSearchValidationError as exc:
        return fail(
            _norm(exc) or "Invalid image search request.",
            error="VALIDATION_ERROR",
        )

    except ImageSearchUnavailableError:
        frappe.log_error(
            frappe.get_traceback(),
            "Image Search Service Unavailable",
        )
        return fail(
            "Image search is temporarily unavailable. Please try again later.",
            error="IMAGE_SEARCH_UNAVAILABLE",
            http_status=503,
        )

    except ImageSearchServiceError:
        frappe.log_error(
            frappe.get_traceback(),
            "Image Search Service Error",
        )
        return fail(
            "Image search failed. Please try again later.",
            error="IMAGE_SEARCH_FAILED",
            http_status=502,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Search Ads by Image Failed",
        )
        return fail(
            "Failed to search ads.",
            error="INTERNAL_ERROR",
        )
