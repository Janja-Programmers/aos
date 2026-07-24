"""Image-search queue tasks.

This module is the boundary between AOS business data and the external
image-search service.

Rules:
- Only Active ads are indexed.
- Indexing is idempotent: replace all vectors for an ad with the current DB images.
- Ad create/update/status flows must not fail because image-search indexing fails.
- The external image-search service owns OpenCLIP, Torch, Qdrant, embeddings, and scoring.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import get_url

from aos.api.ads.media import get_ad_image_url

from aos.integrations.ai.image_search_client import (
    ImageSearchServiceError,
    delete_ad_vectors,
    replace_ad_images,
)

INDEXABLE_AD_STATUSES = {"Active"}
DEFAULT_QUEUE = "short"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MANUAL_BATCH_SIZE = 100
MAX_MANUAL_BATCH_SIZE = 1000
MAX_MANUAL_LIMIT = 100000
SAMPLE_SIZE = 20


class ImageSearchTaskError(Exception):
    """Raised for local task preparation failures before calling image-search."""


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_bool(value: Any) -> bool:
    try:
        return bool(int(value or 0))
    except Exception:
        return bool(value)


def _positive_int(
    value: Any,
    *,
    default: int | None = None,
    minimum: int = 1,
    maximum: int = MAX_MANUAL_LIMIT,
) -> int | None:
    if value is None or value == "":
        return default

    try:
        parsed = int(value)
    except Exception:
        return default

    if parsed < minimum:
        return minimum

    if parsed > maximum:
        return maximum

    return parsed


def _log_error(title: str, *, ad_id: str | None = None) -> None:
    label = title
    if ad_id:
        label = f"{title}: {ad_id}"

    try:
        frappe.log_error(
            frappe.get_traceback(),
            label,
        )
    except Exception:
        # Logging should never break queue execution.
        pass


def _logger():
    try:
        return frappe.logger("aos.image_search")
    except Exception:
        return None


def _log_info(message: str, **context: Any) -> None:
    logger = _logger()
    if not logger:
        return

    try:
        if context:
            logger.info("%s | %s", message, context)
        else:
            logger.info(message)
    except Exception:
        pass


def _normalize_ad_id(ad_id: Any) -> str:
    clean = _clean_str(ad_id)
    if not clean:
        raise ImageSearchTaskError("ad_id is required.")
    return clean


def _absolute_image_url(image_url: str) -> str:
    """Return a URL safe for the image-search service to fetch.

    Existing ad image rows usually store /files/... values. Prefer absolute URLs
    so the image-search service does not need to know Frappe internals. If the
    URL is already absolute, leave it unchanged.
    """

    clean = _clean_str(image_url)

    if not clean:
        return ""

    if clean.startswith("http://") or clean.startswith("https://"):
        return clean

    if clean.startswith("/"):
        try:
            return get_url(clean)
        except Exception:
            return clean

    # Defensive fallback for unusual File values.
    try:
        return get_url(f"/{clean.lstrip('/')}")
    except Exception:
        return clean


def _get_ad_summary(ad_id: str) -> Dict[str, Any] | None:
    row = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        ["name", "status"],
        as_dict=True,
    )

    if not row:
        return None

    return {
        "name": row.name,
        "status": _clean_str(row.status),
    }


def _get_current_ad_images(ad_id: str) -> List[Dict[str, Any]]:
    rows = frappe.get_all(
        "AOS Ad Image",
        filters={
            "parent": ad_id,
            "parenttype": "AOS Ad",
            "parentfield": "images",
        },
        fields=["media", "image", "is_primary", "sort_order", "idx"],
        order_by="sort_order asc, idx asc",
    )

    images: List[Dict[str, Any]] = []

    for row in rows:
        raw_url = get_ad_image_url(row) or _clean_str(row.get("image"))
        if not raw_url:
            continue

        image_url = _absolute_image_url(raw_url)
        if not image_url:
            continue

        images.append(
            {
                "image_url": image_url,
                "is_primary": _to_bool(row.get("is_primary")),
                "sort_order": _to_int(row.get("sort_order"), default=_to_int(row.get("idx"))),
            }
        )

    return images


def _delete_vectors_safely(ad_id: str, *, reason: str) -> Dict[str, Any]:
    try:
        result = delete_ad_vectors(ad_id=ad_id)
        _log_info(
            "Deleted image-search vectors",
            ad_id=ad_id,
            reason=reason,
            result=result,
        )
        return {
            "ok": True,
            "ad_id": ad_id,
            "action": "delete_vectors",
            "reason": reason,
            "result": result,
        }
    except ImageSearchServiceError:
        _log_error("Image-search vector deletion failed", ad_id=ad_id)
        return {
            "ok": False,
            "ad_id": ad_id,
            "action": "delete_vectors",
            "reason": reason,
            "error": "IMAGE_SEARCH_UNAVAILABLE",
        }
    except Exception:
        _log_error("Unexpected image-search vector deletion failure", ad_id=ad_id)
        return {
            "ok": False,
            "ad_id": ad_id,
            "action": "delete_vectors",
            "reason": reason,
            "error": "INTERNAL_ERROR",
        }


def replace_ad_images_task(ad_id: str) -> Dict[str, Any]:
    """Queue task: replace indexed vectors with the ad's current DB images.

    This task intentionally reloads the ad and images from the database. Business
    endpoints should enqueue this task with only ad_id after their transaction is
    saved. That prevents stale request payloads from being indexed.
    """

    clean_ad_id = _normalize_ad_id(ad_id)
    summary = _get_ad_summary(clean_ad_id)

    if not summary:
        return _delete_vectors_safely(
            clean_ad_id,
            reason="ad_not_found",
        )

    status = _clean_str(summary.get("status"))

    if status not in INDEXABLE_AD_STATUSES:
        return _delete_vectors_safely(
            clean_ad_id,
            reason=f"status_not_indexable:{status or 'unknown'}",
        )

    images = _get_current_ad_images(clean_ad_id)

    if not images:
        return _delete_vectors_safely(
            clean_ad_id,
            reason="no_images",
        )

    try:
        result = replace_ad_images(
            ad_id=clean_ad_id,
            images=images,
        )

        _log_info(
            "Replaced image-search vectors",
            ad_id=clean_ad_id,
            image_count=len(images),
            result=result,
        )

        return {
            "ok": True,
            "ad_id": clean_ad_id,
            "action": "replace_images",
            "image_count": len(images),
            "result": result,
        }

    except ImageSearchServiceError:
        _log_error("Image-search image replacement failed", ad_id=clean_ad_id)
        return {
            "ok": False,
            "ad_id": clean_ad_id,
            "action": "replace_images",
            "image_count": len(images),
            "error": "IMAGE_SEARCH_UNAVAILABLE",
        }

    except Exception:
        _log_error("Unexpected image-search image replacement failure", ad_id=clean_ad_id)
        return {
            "ok": False,
            "ad_id": clean_ad_id,
            "action": "replace_images",
            "image_count": len(images),
            "error": "INTERNAL_ERROR",
        }


def delete_ad_vectors_task(ad_id: str) -> Dict[str, Any]:
    """Queue task: delete all indexed vectors for an ad."""

    clean_ad_id = _normalize_ad_id(ad_id)
    return _delete_vectors_safely(
        clean_ad_id,
        reason="explicit_delete",
    )


def enqueue_replace_ad_images(
    ad_id: str,
    *,
    queue: str = DEFAULT_QUEUE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    enqueue_after_commit: bool = True,
) -> Any:
    """Enqueue idempotent image-search indexing for an ad.

    Business endpoints should use this helper instead of calling frappe.enqueue
    with a dotted path directly.
    """

    clean_ad_id = _normalize_ad_id(ad_id)

    return frappe.enqueue(
        "aos.integrations.ai.image_search_tasks.replace_ad_images_task",
        queue=queue,
        timeout=timeout,
        enqueue_after_commit=enqueue_after_commit,
        ad_id=clean_ad_id,
    )


def enqueue_delete_ad_vectors(
    ad_id: str,
    *,
    queue: str = DEFAULT_QUEUE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    enqueue_after_commit: bool = True,
) -> Any:
    """Enqueue deletion of image-search vectors for an ad."""

    clean_ad_id = _normalize_ad_id(ad_id)

    return frappe.enqueue(
        "aos.integrations.ai.image_search_tasks.delete_ad_vectors_task",
        queue=queue,
        timeout=timeout,
        enqueue_after_commit=enqueue_after_commit,
        ad_id=clean_ad_id,
    )


def enqueue_index_refresh_for_status(
    ad_id: str,
    *,
    status: str | None = None,
    queue: str = DEFAULT_QUEUE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    enqueue_after_commit: bool = True,
) -> Any:
    """Enqueue the correct image-search action for an ad status.

    Active ads are indexed. Every other status removes vectors so non-searchable
    ads cannot appear in visual search results.
    """

    clean_status = _clean_str(status)

    if clean_status and clean_status not in INDEXABLE_AD_STATUSES:
        return enqueue_delete_ad_vectors(
            ad_id,
            queue=queue,
            timeout=timeout,
            enqueue_after_commit=enqueue_after_commit,
        )

    return enqueue_replace_ad_images(
        ad_id,
        queue=queue,
        timeout=timeout,
        enqueue_after_commit=enqueue_after_commit,
    )


def _normalize_manual_options(
    *,
    limit: int | None = None,
    batch_size: int | None = None,
) -> tuple[int | None, int]:
    normalized_limit = _positive_int(
        limit,
        default=None,
        minimum=1,
        maximum=MAX_MANUAL_LIMIT,
    )
    normalized_batch_size = _positive_int(
        batch_size,
        default=DEFAULT_MANUAL_BATCH_SIZE,
        minimum=1,
        maximum=MAX_MANUAL_BATCH_SIZE,
    )

    return normalized_limit, int(normalized_batch_size or DEFAULT_MANUAL_BATCH_SIZE)


def _append_sample(sample: List[str], ad_id: str) -> None:
    if len(sample) < SAMPLE_SIZE:
        sample.append(ad_id)


def _commit_enqueue_batch() -> None:
    """Commit queued jobs during manual commands.

    Business endpoints enqueue after commit, but manual bench commands are usually
    read-only operations that only create background jobs. Explicit commits keep
    large reindex runs from holding all queued jobs until the command exits.
    """

    try:
        frappe.db.commit()
    except Exception:
        # Let the caller continue; Frappe will still commit/rollback at command end.
        pass


def _query_indexable_active_ads(*, page_length: int, start: int) -> List[Dict[str, Any]]:
    page_length = max(1, min(int(page_length), MAX_MANUAL_BATCH_SIZE))
    start = max(0, int(start))

    return frappe.db.sql(
        f"""
        SELECT DISTINCT ad.name
        FROM `tabAOS Ad` ad
        INNER JOIN `tabAOS Seller` seller
            ON seller.name = ad.seller
        INNER JOIN `tabAOS Ad Image` img
            ON img.parent = ad.name
            AND img.parenttype = 'AOS Ad'
            AND img.parentfield = 'images'
        WHERE ad.status = 'Active'
            AND seller.status = 'Active'
            AND (ad.expires_on IS NULL OR ad.expires_on >= CURRENT_DATE())
            AND (COALESCE(img.media, '') != '' OR COALESCE(img.image, '') != '')
        ORDER BY ad.modified DESC
        LIMIT %s OFFSET %s
        """,
        (page_length, start),
        as_dict=True,
    )


def _query_unindexable_ads(*, page_length: int, start: int) -> List[Dict[str, Any]]:
    """Return ads that should not have vectors.

    This includes non-Active ads and Active ads with no saved images. Vector
    deletion is idempotent, so it is safe if some returned ads have no vectors.
    """

    page_length = max(1, min(int(page_length), MAX_MANUAL_BATCH_SIZE))
    start = max(0, int(start))

    return frappe.db.sql(
        f"""
        SELECT ad.name, ad.status
        FROM `tabAOS Ad` ad
        LEFT JOIN `tabAOS Seller` seller
            ON seller.name = ad.seller
        LEFT JOIN `tabAOS Ad Image` img
            ON img.parent = ad.name
            AND img.parenttype = 'AOS Ad'
            AND img.parentfield = 'images'
            AND (COALESCE(img.media, '') != '' OR COALESCE(img.image, '') != '')
        GROUP BY ad.name, ad.status, seller.status, ad.expires_on, ad.modified
        HAVING COALESCE(ad.status, '') != 'Active'
            OR COALESCE(seller.status, '') != 'Active'
            OR (ad.expires_on IS NOT NULL AND ad.expires_on < CURRENT_DATE())
            OR COUNT(img.name) = 0
        ORDER BY ad.modified DESC
        LIMIT %s OFFSET %s
        """,
        (page_length, start),
        as_dict=True,
    )


def reindex_active_ads(
    limit: int | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    queue: str = DEFAULT_QUEUE,
) -> Dict[str, Any]:
    """Manually enqueue image-search reindexing for Active ads with images.

    Usage:
        bench --site your-site execute aos.integrations.ai.image_search_tasks.reindex_active_ads

    Limited run:
        bench --site your-site execute aos.integrations.ai.image_search_tasks.reindex_active_ads --kwargs '{"limit": 100}'

    Dry run:
        bench --site your-site execute aos.integrations.ai.image_search_tasks.reindex_active_ads --kwargs '{"dry_run": true, "limit": 100}'
    """

    normalized_limit, normalized_batch_size = _normalize_manual_options(
        limit=limit,
        batch_size=batch_size,
    )

    scanned = 0
    queued = 0
    failed = 0
    sample: List[str] = []
    offset = 0

    while True:
        remaining = None if normalized_limit is None else normalized_limit - scanned
        if remaining is not None and remaining <= 0:
            break

        page_length = normalized_batch_size if remaining is None else min(normalized_batch_size, remaining)
        rows = _query_indexable_active_ads(page_length=page_length, start=offset)
        if not rows:
            break

        for row in rows:
            ad_id = _clean_str(row.get("name"))
            if not ad_id:
                continue

            scanned += 1
            _append_sample(sample, ad_id)

            if dry_run:
                queued += 1
                continue

            try:
                enqueue_replace_ad_images(
                    ad_id,
                    queue=queue,
                    enqueue_after_commit=False,
                )
                queued += 1
            except Exception:
                failed += 1
                _log_error("Failed to enqueue image-search reindex", ad_id=ad_id)

        if not dry_run:
            _commit_enqueue_batch()

        offset += len(rows)

        if len(rows) < page_length:
            break

    return {
        "ok": failed == 0,
        "action": "reindex_active_ads",
        "dry_run": bool(dry_run),
        "scanned": scanned,
        "queued": queued,
        "failed": failed,
        "limit": normalized_limit,
        "batch_size": normalized_batch_size,
        "queue": queue,
        "sample_ad_ids": sample,
    }


def delete_vectors_for_unindexable_ads(
    limit: int | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    queue: str = DEFAULT_QUEUE,
) -> Dict[str, Any]:
    """Manually enqueue vector cleanup for ads that should not be searchable.

    This includes:
    - non-Active ads
    - Active ads without images

    Vector deletion is idempotent, so this is safe to run repeatedly.
    """

    normalized_limit, normalized_batch_size = _normalize_manual_options(
        limit=limit,
        batch_size=batch_size,
    )

    scanned = 0
    queued = 0
    failed = 0
    sample: List[str] = []
    offset = 0

    while True:
        remaining = None if normalized_limit is None else normalized_limit - scanned
        if remaining is not None and remaining <= 0:
            break

        page_length = normalized_batch_size if remaining is None else min(normalized_batch_size, remaining)
        rows = _query_unindexable_ads(page_length=page_length, start=offset)
        if not rows:
            break

        for row in rows:
            ad_id = _clean_str(row.get("name"))
            if not ad_id:
                continue

            scanned += 1
            _append_sample(sample, ad_id)

            if dry_run:
                queued += 1
                continue

            try:
                enqueue_delete_ad_vectors(
                    ad_id,
                    queue=queue,
                    enqueue_after_commit=False,
                )
                queued += 1
            except Exception:
                failed += 1
                _log_error("Failed to enqueue image-search cleanup", ad_id=ad_id)

        if not dry_run:
            _commit_enqueue_batch()

        offset += len(rows)

        if len(rows) < page_length:
            break

    return {
        "ok": failed == 0,
        "action": "delete_vectors_for_unindexable_ads",
        "dry_run": bool(dry_run),
        "scanned": scanned,
        "queued": queued,
        "failed": failed,
        "limit": normalized_limit,
        "batch_size": normalized_batch_size,
        "queue": queue,
        "sample_ad_ids": sample,
    }


def delete_vectors_for_non_active_ads(
    limit: int | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    queue: str = DEFAULT_QUEUE,
) -> Dict[str, Any]:
    """Backward-compatible maintenance helper.

    Use delete_vectors_for_unindexable_ads instead. This helper remains because
    earlier Phase 1 notes referenced it.
    """

    normalized_limit, normalized_batch_size = _normalize_manual_options(
        limit=limit,
        batch_size=batch_size,
    )

    scanned = 0
    queued = 0
    failed = 0
    sample: List[str] = []
    offset = 0

    while True:
        remaining = None if normalized_limit is None else normalized_limit - scanned
        if remaining is not None and remaining <= 0:
            break

        page_length = normalized_batch_size if remaining is None else min(normalized_batch_size, remaining)
        rows = frappe.db.sql(
            f"""
            SELECT ad.name, ad.status
            FROM `tabAOS Ad` ad
            WHERE COALESCE(ad.status, '') != 'Active'
            ORDER BY ad.modified DESC
            LIMIT %s OFFSET %s
            """,
            (page_length, offset),
            as_dict=True,
        )
        if not rows:
            break

        for row in rows:
            ad_id = _clean_str(row.get("name"))
            if not ad_id:
                continue

            scanned += 1
            _append_sample(sample, ad_id)

            if dry_run:
                queued += 1
                continue

            try:
                enqueue_delete_ad_vectors(
                    ad_id,
                    queue=queue,
                    enqueue_after_commit=False,
                )
                queued += 1
            except Exception:
                failed += 1
                _log_error("Failed to enqueue non-Active image-search cleanup", ad_id=ad_id)

        if not dry_run:
            _commit_enqueue_batch()

        offset += len(rows)

        if len(rows) < page_length:
            break

    return {
        "ok": failed == 0,
        "action": "delete_vectors_for_non_active_ads",
        "dry_run": bool(dry_run),
        "scanned": scanned,
        "queued": queued,
        "failed": failed,
        "limit": normalized_limit,
        "batch_size": normalized_batch_size,
        "queue": queue,
        "sample_ad_ids": sample,
    }


def rebuild_image_search_index(
    limit: int | None = None,
    cleanup_limit: int | None = None,
    reindex_limit: int | None = None,
    batch_size: int | None = None,
    dry_run: bool = False,
    queue: str = DEFAULT_QUEUE,
) -> Dict[str, Any]:
    """Manual full rebuild helper for image-search index state.

    This queues two safe operations:
    1. Delete vectors for ads that should not be searchable.
    2. Replace vectors for Active ads that have images.

    Usage:
        bench --site your-site execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index

    Dry run:
        bench --site your-site execute aos.integrations.ai.image_search_tasks.rebuild_image_search_index --kwargs '{"dry_run": true, "limit": 100}'
    """

    effective_cleanup_limit = cleanup_limit if cleanup_limit is not None else limit
    effective_reindex_limit = reindex_limit if reindex_limit is not None else limit

    cleanup_result = delete_vectors_for_unindexable_ads(
        limit=effective_cleanup_limit,
        batch_size=batch_size,
        dry_run=dry_run,
        queue=queue,
    )
    reindex_result = reindex_active_ads(
        limit=effective_reindex_limit,
        batch_size=batch_size,
        dry_run=dry_run,
        queue=queue,
    )

    return {
        "ok": bool(cleanup_result.get("ok")) and bool(reindex_result.get("ok")),
        "action": "rebuild_image_search_index",
        "dry_run": bool(dry_run),
        "cleanup": cleanup_result,
        "reindex": reindex_result,
    }
