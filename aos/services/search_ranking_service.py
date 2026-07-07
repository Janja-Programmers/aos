"""Frappe-side orchestration for external search/ranking/recommendation.

Frappe owns business truth, permissions, visibility, and hydration.
The external search-ranking service owns candidate retrieval, fast text matching,
feed scoring, and Redis-backed index state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime

from aos.utils.aos_config import clean_url, get_env, get_env_bool, get_env_int, get_first_env


class SearchRankingError(RuntimeError):
    """Raised when search/ranking orchestration fails."""


@dataclass(frozen=True)
class SearchRankingConfig:
    service_url: str
    service_secret: str
    callback_secret: str
    callback_url: str
    request_timeout_seconds: int
    max_attempts: int
    queue: str
    dispatcher_timeout_seconds: int
    enabled: bool
    fail_open: bool
    use_ads_search: bool
    use_shorts_feed: bool


def get_search_ranking_config() -> SearchRankingConfig:
    service_url = clean_url(
        get_first_env(
            "SEARCH_RANKING_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('SEARCH_RANKING_SERVICE_PORT', '8150')}",
        ),
        default="http://127.0.0.1:8150",
    )
    callback_url = clean_url(get_env("SEARCH_RANKING_CALLBACK_URL"))
    if not callback_url:
        domain = get_env("AOS_API_DOMAIN")
        if domain:
            callback_url = f"https://{domain}/api/method/aos.api.search_ranking.handle_callback"
        else:
            callback_url = "http://127.0.0.1:8000/api/method/aos.api.search_ranking.handle_callback"
    return SearchRankingConfig(
        service_url=service_url,
        service_secret=get_env("SEARCH_RANKING_SERVICE_SECRET", "") or "",
        callback_secret=get_env("SEARCH_RANKING_SERVICE_CALLBACK_SECRET", "") or "",
        callback_url=callback_url,
        request_timeout_seconds=get_env_int("SEARCH_RANKING_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120),
        max_attempts=get_env_int("SEARCH_RANKING_MAX_RETRIES", 3, min_value=1, max_value=10),
        queue=get_env("SEARCH_RANKING_FRAPPE_QUEUE", "long") or "long",
        dispatcher_timeout_seconds=get_env_int("SEARCH_RANKING_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800),
        enabled=get_env_bool("SEARCH_RANKING_ENABLED", True),
        fail_open=get_env_bool("SEARCH_RANKING_FAIL_OPEN", True),
        use_ads_search=get_env_bool("SEARCH_RANKING_USE_ADS_SEARCH", True),
        use_shorts_feed=get_env_bool("SEARCH_RANKING_USE_SHORTS_FEED", True),
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_signature(secret: str, payload: bytes) -> str:
    digest = hmac.new(str(secret or "").encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not str(secret or "").strip():
        return False
    if not signature:
        return False
    return hmac.compare_digest(build_signature(secret, payload), str(signature).strip())


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _clean(value: Any) -> str:
    return str(value or "").strip()


def create_search_index_job(
    *,
    target_doctype: str,
    target_name: str,
    index_kind: str,
    action: str = "upsert",
    source: str = "manual",
    document: dict[str, Any] | None = None,
    target_owner: str | None = None,
    enqueue: bool = True,
) -> object | None:
    config = get_search_ranking_config()
    if not config.enabled:
        return None
    target_doctype = _clean(target_doctype)
    target_name = _clean(target_name)
    index_kind = _clean(index_kind).lower()
    action = _clean(action).lower() or "upsert"
    if action not in {"upsert", "delete"}:
        raise SearchRankingError("Invalid search index action")
    if not target_doctype or not target_name:
        raise SearchRankingError("Search index target is required")
    if action != "delete" and not frappe.db.exists(target_doctype, target_name):
        raise SearchRankingError("Search index target does not exist")

    job = frappe.get_doc({
        "doctype": "AOS Search Index Job",
        "target_doctype": target_doctype,
        "target_name": target_name,
        "target_owner": target_owner,
        "index_kind": index_kind,
        "action": action,
        "source": source,
        "status": "Queued",
        "attempt_count": 0,
        "max_attempts": config.max_attempts,
        "idempotency_key": uuid.uuid4().hex,
        "document_json": _json_dumps(document or {}),
    })
    job.insert(ignore_permissions=True)
    frappe.db.commit()
    if enqueue:
        enqueue_search_index_dispatch(job.name)
    return job


def enqueue_search_index_dispatch(search_job_id: str) -> None:
    config = get_search_ranking_config()
    frappe.enqueue(
        "aos.tasks.search_ranking.dispatch_search_index_job",
        search_job_id=search_job_id,
        queue=config.queue,
        timeout=config.dispatcher_timeout_seconds,
        enqueue_after_commit=True,
        job_name=f"dispatch-search-index:{search_job_id}",
    )


def build_search_index_job_payload(job) -> dict[str, Any]:
    return {
        "job_id": job.name,
        "action": job.action,
        "target": {
            "doctype": job.target_doctype,
            "name": job.target_name,
            "owner": job.target_owner,
            "index_kind": job.index_kind,
            "source": job.source,
        },
        "document": _json_loads(job.document_json, {}),
        "callback_url": get_search_ranking_config().callback_url,
    }


def dispatch_search_index_job(search_job_id: str) -> object:
    job = frappe.get_doc("AOS Search Index Job", search_job_id)
    if job.status in {"Indexed", "Deleted", "Cancelled"}:
        return job
    if job.status == "Processing" and getattr(job, "service_job_id", None):
        return job

    config = get_search_ranking_config()
    if not config.enabled:
        job.status = "Cancelled"
        job.last_error = "Search/ranking is disabled"
        job.completed_at = now_datetime()
        job.save(ignore_permissions=True)
        frappe.db.commit()
        return job

    job.status = "Dispatching"
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.last_error = None
    job.dispatched_at = now_datetime()
    job.save(ignore_permissions=True)
    frappe.db.commit()

    payload = build_search_index_job_payload(job)
    job.request_payload = json.dumps(payload, ensure_ascii=False, default=str)
    job.save(ignore_permissions=True)
    frappe.db.commit()

    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Search-Signature": build_signature(config.service_secret, body),
    }
    try:
        response = requests.post(f"{config.service_url}/jobs", data=body, headers=headers, timeout=config.request_timeout_seconds)
        response.raise_for_status()
        data = response.json() if response.content else {}
        job.reload()
        job.status = "Processing"
        job.service_job_id = str(data.get("service_job_id") or data.get("job_id") or "")
        job.started_at = now_datetime()
        job.save(ignore_permissions=True)
        frappe.db.commit()
        return job
    except Exception as exc:
        mark_search_index_job_failed(job.name, str(exc) or "Failed to dispatch search index job")
        raise


def handle_search_index_callback(payload: dict[str, Any]) -> object:
    job_id = _clean(payload.get("job_id"))
    if not job_id:
        raise SearchRankingError("job_id is required")
    if not frappe.db.exists("AOS Search Index Job", job_id):
        raise SearchRankingError("Search index job not found")
    job = frappe.get_doc("AOS Search Index Job", job_id)
    incoming_status = _clean(payload.get("status")).lower()
    terminal_statuses = {"Indexed", "Deleted", "Failed"}
    expected_success_status = "Deleted" if job.action == "delete" else "Indexed"
    if job.status in terminal_statuses:
        if job.status == expected_success_status and incoming_status in {"completed", "ready"}:
            return job
        if job.status == "Failed" and incoming_status == "failed":
            return job
        raise SearchRankingError(f"Search/ranking job is already {job.status}")
    if incoming_status in {"completed", "ready"}:
        job.status = expected_success_status
        job.indexed = 1 if job.action != "delete" else 0
        job.score = float(payload.get("score") or 0)
        job.response_payload = json.dumps(payload, ensure_ascii=False, default=str)
        job.callback_received_at = now_datetime()
        job.completed_at = now_datetime()
        job.last_error = None
        job.save(ignore_permissions=True)
        frappe.db.commit()
        return job
    if incoming_status == "failed":
        return mark_search_index_job_failed(job.name, _clean(payload.get("error")) or "Search/ranking job failed")
    raise SearchRankingError("Invalid search/ranking callback status")


def mark_search_index_job_failed(job_id: str, error: str) -> object:
    job = frappe.get_doc("AOS Search Index Job", job_id)
    job.status = "Failed"
    job.completed_at = now_datetime()
    job.last_error = str(error or "Search/ranking job failed")[:1000]
    job.save(ignore_permissions=True)
    frappe.db.commit()
    return job


def build_ad_index_document(ad_id: str) -> dict[str, Any]:
    ad = frappe.get_doc("AOS Ad", ad_id)
    seller_status = frappe.db.get_value("AOS Seller", ad.seller, "status") or ""
    seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user") or ad.seller
    seller_name = frappe.db.get_value("AOS Seller", ad.seller, "shop_name") or seller_user
    seller_verified = frappe.db.get_value("AOS Profile", {"user": seller_user}, "is_verified") or 0
    keywords: list[str] = []
    for value in [getattr(ad, "title", ""), getattr(ad, "category", ""), getattr(ad, "location", ""), getattr(ad, "country", "")]:
        if _clean(value):
            keywords.append(_clean(value))
    return {
        "id": ad.name,
        "name": ad.name,
        "title": getattr(ad, "title", "") or "",
        "description": getattr(ad, "description", "") or "",
        "status": getattr(ad, "status", "") or "",
        "country": getattr(ad, "country", "") or "",
        "location": getattr(ad, "location", "") or "",
        "category": getattr(ad, "category", "") or "",
        "seller": getattr(ad, "seller", "") or "",
        "seller_status": seller_status,
        "seller_name": seller_name,
        "seller_verified": int(seller_verified or 0),
        "price_type": getattr(ad, "price_type", "") or "",
        "price": float(getattr(ad, "price", 0) or 0),
        "currency": getattr(ad, "currency", "") or "",
        "average_rating": float(getattr(ad, "average_rating", 0) or 0),
        "total_reviews": int(getattr(ad, "total_reviews", 0) or 0),
        "view_count": int(getattr(ad, "view_count", 0) or 0),
        "wishlist_count": int(getattr(ad, "wishlist_count", 0) or 0),
        "creation": str(getattr(ad, "creation", "") or ""),
        "modified": str(getattr(ad, "modified", "") or ""),
        "keywords": keywords,
    }


def build_short_index_document(short_id: str) -> dict[str, Any]:
    short = frappe.get_doc("AOS Short", short_id)
    return {
        "id": short.name,
        "name": short.name,
        "owner": getattr(short, "owner", "") or "",
        "status": getattr(short, "status", "") or "",
        "visibility_status": getattr(short, "visibility_status", "") or "",
        "approval_status": getattr(short, "approval_status", "") or "",
        "content_mode": getattr(short, "content_mode", "") or "",
        "audience": getattr(short, "audience", "") or "",
        "country": getattr(short, "country", "") or "",
        "seller": getattr(short, "seller", "") or "",
        "ad": getattr(short, "ad", "") or "",
        "caption": getattr(short, "caption", "") or "",
        "hashtags": getattr(short, "hashtags", "") or "",
        "ranking_score": float(getattr(short, "ranking_score", 0) or 0),
        "view_count": int(getattr(short, "view_count", 0) or 0),
        "like_count": int(getattr(short, "like_count", 0) or 0),
        "comment_count": int(getattr(short, "comment_count", 0) or 0),
        "share_count": int(getattr(short, "share_count", 0) or 0),
        "save_count": int(getattr(short, "save_count", 0) or 0),
        "repost_count": int(getattr(short, "repost_count", 0) or 0),
        "creation": str(getattr(short, "creation", "") or ""),
        "modified": str(getattr(short, "modified", "") or ""),
    }


def enqueue_ad_search_index(ad_id: str, *, source: str = "ad_update", enqueue: bool = True) -> object | None:
    action = "upsert"
    document: dict[str, Any] = {}
    target_owner = None
    if frappe.db.exists("AOS Ad", ad_id):
        document = build_ad_index_document(ad_id)
        target_owner = frappe.db.get_value("AOS Seller", document.get("seller"), "user") or document.get("seller")
        if document.get("status") != "Active" or document.get("seller_status") != "Active":
            action = "delete"
    else:
        action = "delete"
    return create_search_index_job(
        target_doctype="AOS Ad",
        target_name=ad_id,
        target_owner=target_owner,
        index_kind="ad",
        action=action,
        source=source,
        document=document,
        enqueue=enqueue,
    )


def enqueue_short_search_index(short_id: str, *, source: str = "short_update", enqueue: bool = True) -> object | None:
    action = "upsert"
    document: dict[str, Any] = {}
    target_owner = None
    if frappe.db.exists("AOS Short", short_id):
        document = build_short_index_document(short_id)
        target_owner = document.get("owner")
        if document.get("status") != "ready" or document.get("visibility_status") != "visible":
            action = "delete"
    else:
        action = "delete"
    return create_search_index_job(
        target_doctype="AOS Short",
        target_name=short_id,
        target_owner=target_owner,
        index_kind="short",
        action=action,
        source=source,
        document=document,
        enqueue=enqueue,
    )


def _request_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = get_search_ranking_config()
    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Search-Signature": build_signature(config.service_secret, body),
    }
    response = requests.post(f"{config.service_url}/{path.lstrip('/')}", data=body, headers=headers, timeout=config.request_timeout_seconds)
    response.raise_for_status()
    data = response.json() if response.content else {}
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise SearchRankingError(str(data.get("message") or data.get("error") or "Search/ranking service failed"))
    return data


def search_ad_candidates(*, q: str = "", filters: dict[str, Any] | None = None, limit: int = 20, offset: int = 0) -> list[str]:
    config = get_search_ranking_config()
    if not config.enabled or not config.use_ads_search:
        raise SearchRankingError("Search/ranking ads search is disabled")
    data = _request_json("/ads/search", {"q": q, "filters": filters or {}, "limit": limit, "offset": offset})
    return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def short_feed_candidates(*, viewer: str | None = None, content_mode: str | None = None, country: str | None = None, limit: int = 20, offset: int = 0) -> list[str]:
    config = get_search_ranking_config()
    if not config.enabled or not config.use_shorts_feed:
        raise SearchRankingError("Search/ranking shorts feed is disabled")
    data = _request_json("/shorts/feed", {"viewer": viewer, "content_mode": content_mode, "country": country, "limit": limit, "offset": offset})
    return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def related_ad_candidates(*, ad_id: str, limit: int = 20) -> list[str]:
    config = get_search_ranking_config()
    if not config.enabled:
        raise SearchRankingError("Search/ranking service is disabled")
    data = _request_json("/ads/related", {"ad_id": ad_id, "limit": limit})
    return [_clean(row.get("id")) for row in data.get("items") or [] if _clean(row.get("id"))]


def reindex_active_ads(limit: int = 1000, enqueue: bool = True) -> dict[str, Any]:
    rows = frappe.get_all("AOS Ad", filters={"status": "Active"}, fields=["name"], limit_page_length=max(1, int(limit)), order_by="modified desc")
    queued = 0
    failed = 0
    for row in rows:
        try:
            enqueue_ad_search_index(row.name, source="manual_reindex", enqueue=enqueue)
            queued += 1
        except Exception:
            failed += 1
            frappe.log_error(frappe.get_traceback(), f"Search/ranking ad reindex failed: {row.name}")
    frappe.db.commit()
    return {"ok": failed == 0, "target": "ads", "scanned": len(rows), "queued": queued, "failed": failed}


def reindex_visible_shorts(limit: int = 1000, enqueue: bool = True) -> dict[str, Any]:
    rows = frappe.get_all("AOS Short", filters={"status": "ready", "visibility_status": "visible"}, fields=["name"], limit_page_length=max(1, int(limit)), order_by="modified desc")
    queued = 0
    failed = 0
    for row in rows:
        try:
            enqueue_short_search_index(row.name, source="manual_reindex", enqueue=enqueue)
            queued += 1
        except Exception:
            failed += 1
            frappe.log_error(frappe.get_traceback(), f"Search/ranking short reindex failed: {row.name}")
    frappe.db.commit()
    return {"ok": failed == 0, "target": "shorts", "scanned": len(rows), "queued": queued, "failed": failed}
