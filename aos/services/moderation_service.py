"""Frappe-side orchestration for external content moderation.

Frappe owns business records, permissions, final decisions, and audit history.
The external moderation service owns analysis execution and returns signals.
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

from aos.services.media.media_service import MediaService
from aos.utils.aos_config import clean_url, get_env, get_env_bool, get_env_int, get_first_env


class ModerationError(RuntimeError):
    """Raised when moderation orchestration fails."""


@dataclass(frozen=True)
class ModerationConfig:
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


def get_moderation_config() -> ModerationConfig:
    service_url = clean_url(
        get_first_env(
            "MODERATION_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('MODERATION_SERVICE_PORT', '8140')}",
        ),
        default="http://127.0.0.1:8140",
    )

    callback_url = clean_url(get_env("MODERATION_CALLBACK_URL"))
    if not callback_url:
        domain = get_env("AOS_API_DOMAIN")
        if domain:
            callback_url = f"https://{domain}/api/method/aos.api.moderation.handle_callback"
        else:
            callback_url = "http://127.0.0.1:8000/api/method/aos.api.moderation.handle_callback"

    return ModerationConfig(
        service_url=service_url,
        service_secret=get_env("MODERATION_SERVICE_SECRET", "") or "",
        callback_secret=get_env("MODERATION_SERVICE_CALLBACK_SECRET", "") or "",
        callback_url=callback_url,
        request_timeout_seconds=get_env_int("MODERATION_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120),
        max_attempts=get_env_int("MODERATION_MAX_RETRIES", 3, min_value=1, max_value=10),
        queue=get_env("MODERATION_FRAPPE_QUEUE", "long") or "long",
        dispatcher_timeout_seconds=get_env_int("MODERATION_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800),
        enabled=get_env_bool("MODERATION_ENABLED", True),
        fail_open=get_env_bool("MODERATION_FAIL_OPEN", False),
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_signature(secret: str, payload: bytes) -> str:
    digest = hmac.new(str(secret or "").encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    return hmac.compare_digest(build_signature(secret, payload), str(signature).strip())


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def build_text_item(field: str, value: Any, *, content_type: str = "text/plain") -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    return {"field": str(field or "text"), "text": text, "content_type": content_type}


def build_media_item(media_id: str, *, field: str = "media") -> dict[str, Any]:
    media_id = str(media_id or "").strip()
    if not media_id:
        return {}
    media = MediaService().get_media_doc(media_id)
    return {
        "field": field,
        "media_id": media.name,
        "purpose": media.purpose,
        "bucket": media.bucket,
        "object_key": media.object_key,
        "content_type": media.content_type,
        "size_bytes": int(media.size_bytes or 0),
        "visibility": media.visibility,
        "public_url": getattr(media, "public_url", "") or "",
        "width": int(getattr(media, "width", 0) or 0),
        "height": int(getattr(media, "height", 0) or 0),
        "duration_seconds": float(getattr(media, "duration_seconds", 0) or 0),
    }


def create_moderation_job(
    *,
    target_doctype: str,
    target_name: str,
    content_kind: str,
    source: str,
    text_items: list[dict[str, Any]] | None = None,
    media_items: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    target_owner: str | None = None,
    enqueue: bool = True,
) -> object | None:
    """Create a persistent moderation job and optionally enqueue dispatch."""
    config = get_moderation_config()
    if not config.enabled:
        return None

    target_doctype = str(target_doctype or "").strip()
    target_name = str(target_name or "").strip()
    content_kind = str(content_kind or "").strip().lower()
    source = str(source or "").strip()
    if not target_doctype or not target_name:
        raise ModerationError("Moderation target is required")
    if not frappe.db.exists(target_doctype, target_name):
        raise ModerationError("Moderation target does not exist")

    cleaned_text = [item for item in (text_items or []) if item and str(item.get("text") or "").strip()]
    cleaned_media = [item for item in (media_items or []) if item and str(item.get("media_id") or item.get("object_key") or "").strip()]

    job = frappe.get_doc(
        {
            "doctype": "AOS Moderation Job",
            "target_doctype": target_doctype,
            "target_name": target_name,
            "target_owner": target_owner,
            "content_kind": content_kind,
            "source": source,
            "status": "Queued",
            "decision": "pending",
            "attempt_count": 0,
            "max_attempts": config.max_attempts,
            "idempotency_key": uuid.uuid4().hex,
            "text_items_json": _json_dumps(cleaned_text),
            "media_items_json": _json_dumps(cleaned_media),
            "context_json": _json_dumps(context or {}),
        }
    )
    job.insert(ignore_permissions=True)
    frappe.db.commit()

    if enqueue:
        enqueue_moderation_dispatch(job.name)

    return job


def enqueue_moderation_dispatch(moderation_job_id: str) -> None:
    """Enqueue lightweight Frappe dispatcher for a moderation job."""
    config = get_moderation_config()
    frappe.enqueue(
        "aos.tasks.moderation.dispatch_moderation_job",
        moderation_job_id=moderation_job_id,
        queue=config.queue,
        timeout=config.dispatcher_timeout_seconds,
        enqueue_after_commit=True,
        job_name=f"dispatch-moderation:{moderation_job_id}",
    )


def dispatch_moderation_job(moderation_job_id: str) -> object:
    """Lightweight Frappe RQ dispatcher. Does not analyze content."""
    job = frappe.get_doc("AOS Moderation Job", moderation_job_id)
    if job.status in {"Allowed", "Review Required", "Rejected"}:
        return job
    if job.status == "Processing" and getattr(job, "service_job_id", None):
        return job
    if job.status == "Cancelled":
        return job

    config = get_moderation_config()
    if not config.enabled:
        job.status = "Cancelled"
        job.last_error = "Moderation is disabled"
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

    payload = build_moderation_job_payload(job)
    job.request_payload = json.dumps(payload, ensure_ascii=False, default=str)
    job.save(ignore_permissions=True)
    frappe.db.commit()

    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Moderation-Signature": build_signature(config.service_secret, body),
    }

    try:
        response = requests.post(
            f"{config.service_url}/jobs",
            data=body,
            headers=headers,
            timeout=config.request_timeout_seconds,
        )
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
        mark_moderation_job_failed(job.name, str(exc) or "Failed to dispatch moderation job")
        raise


def build_moderation_job_payload(job) -> dict[str, Any]:
    return {
        "job_id": job.name,
        "target": {
            "doctype": job.target_doctype,
            "name": job.target_name,
            "owner": job.target_owner,
            "content_kind": job.content_kind,
            "source": job.source,
        },
        "text_items": _json_loads(job.text_items_json, []),
        "media_items": _json_loads(job.media_items_json, []),
        "context": _json_loads(job.context_json, {}),
        "callback_url": get_moderation_config().callback_url,
    }


def handle_moderation_callback(payload: dict[str, Any]) -> object:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ModerationError("job_id is required")
    if not frappe.db.exists("AOS Moderation Job", job_id):
        raise ModerationError("Moderation job not found")

    job = frappe.get_doc("AOS Moderation Job", job_id)
    incoming_status = str(payload.get("status") or "").strip().lower()

    if job.status in {"Allowed", "Review Required", "Rejected"} and incoming_status in {"completed", "ready"}:
        return job

    if incoming_status in {"completed", "ready"}:
        return mark_moderation_job_completed(job, payload)
    if incoming_status == "failed":
        return mark_moderation_job_failed(job.name, str(payload.get("error") or "Moderation failed"))

    raise ModerationError("Invalid moderation callback status")


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    if decision in {"allow", "allowed", "approve", "approved"}:
        return "allow"
    if decision in {"reject", "rejected", "deny", "blocked"}:
        return "reject"
    if decision in {"review", "hold", "manual_review", "needs_review"}:
        return "review"
    return "review"


def mark_moderation_job_completed(job, payload: dict[str, Any]) -> object:
    decision = _normalize_decision(payload.get("decision"))
    labels = payload.get("labels") if isinstance(payload.get("labels"), list) else []
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    risk_score = float(payload.get("risk_score") or 0)

    job.decision = decision
    job.status = {
        "allow": "Allowed",
        "review": "Review Required",
        "reject": "Rejected",
    }.get(decision, "Review Required")
    job.labels_json = _json_dumps(labels)
    job.scores_json = _json_dumps(scores)
    job.reasons_json = _json_dumps(reasons)
    job.risk_score = risk_score
    job.response_payload = json.dumps(payload, ensure_ascii=False, default=str)
    job.callback_received_at = now_datetime()
    job.completed_at = now_datetime()
    job.last_error = None
    job.save(ignore_permissions=True)

    apply_moderation_decision(job, decision, reasons)
    frappe.db.commit()
    return job


def mark_moderation_job_failed(job_id: str, error: str) -> object:
    job = frappe.get_doc("AOS Moderation Job", job_id)
    error_text = str(error or "Moderation failed")[:1000]
    job.status = "Failed"
    job.decision = "failed"
    job.completed_at = now_datetime()
    job.last_error = error_text
    job.save(ignore_permissions=True)

    config = get_moderation_config()
    if not config.fail_open:
        _hold_target_for_review(job, error_text)

    frappe.db.commit()
    return job


def apply_moderation_decision(job, decision: str, reasons: list[Any] | None = None) -> None:
    if job.target_doctype == "AOS Ad":
        _apply_ad_decision(job, decision, reasons or [])
    elif job.target_doctype == "AOS Review":
        _apply_review_decision(job, decision, reasons or [])
    elif job.target_doctype == "AOS Short":
        _apply_short_decision(job, decision, reasons or [])


def _reason_text(reasons: list[Any], fallback: str) -> str:
    cleaned = [str(item).strip() for item in reasons or [] if str(item or "").strip()]
    return "; ".join(cleaned)[:1000] if cleaned else fallback


def _apply_ad_decision(job, decision: str, reasons: list[Any]) -> None:
    ad = frappe.get_doc("AOS Ad", job.target_name)
    if ad.status in {"Deleted", "Sold", "Expired", "Suspended"}:
        return

    seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user") or ad.seller
    if decision == "allow":
        ad.status = "Active"
        ad.decline_reason = None
        ad.save(ignore_permissions=True)
        _notify_ad_approved(user=seller_user, ad=ad)
        _enqueue_ad_index(ad)
    elif decision == "reject":
        ad.status = "Declined"
        ad.decline_reason = _reason_text(reasons, "Rejected by content moderation.")
        ad.save(ignore_permissions=True)
        _notify_ad_rejected(user=seller_user, ad=ad)
        _enqueue_ad_index(ad)
    else:
        ad.status = "Reviewing"
        ad.decline_reason = _reason_text(reasons, "Requires manual content review.")
        ad.save(ignore_permissions=True)
        _enqueue_ad_index(ad)


def _apply_review_decision(job, decision: str, reasons: list[Any]) -> None:
    review = frappe.get_doc("AOS Review", job.target_name)
    if review.status not in {"Pending", "Approved", "Rejected"}:
        return
    if decision == "allow":
        review.status = "Approved"
    elif decision == "reject":
        review.status = "Rejected"
        if review.meta.has_field("review_notes"):
            review.review_notes = _reason_text(reasons, "Rejected by content moderation.")
    else:
        review.status = "Pending"
        if review.meta.has_field("review_notes"):
            review.review_notes = _reason_text(reasons, "Requires manual content review.")
    review.save(ignore_permissions=True)


def _apply_short_decision(job, decision: str, reasons: list[Any]) -> None:
    short = frappe.get_doc("AOS Short", job.target_name)
    context = _json_loads(job.context_json, {})
    was_visible = bool(context.get("was_visible"))

    if short.status == "deleted" or short.visibility_status == "deleted":
        return

    if decision == "allow":
        short.approval_status = "auto_approved"
        short.visibility_status = "visible"
        short.hidden_reason = None
        short.save(ignore_permissions=True)
        if not was_visible:
            try:
                from aos.services.notification_service import NotificationService
                NotificationService.notify_new_short(actor=short.owner, short_id=short.name)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Short moderation notification failed")
    elif decision == "reject":
        short.approval_status = "rejected"
        short.visibility_status = "hidden"
        short.hidden_reason = _reason_text(reasons, "Rejected by content moderation.")
        short.save(ignore_permissions=True)
    else:
        short.approval_status = "flagged"
        short.visibility_status = "hidden"
        short.hidden_reason = _reason_text(reasons, "Requires manual content review.")
        short.save(ignore_permissions=True)


def _hold_target_for_review(job, error_text: str) -> None:
    if job.target_doctype == "AOS Ad":
        if frappe.db.exists("AOS Ad", job.target_name):
            frappe.db.set_value("AOS Ad", job.target_name, {"status": "Reviewing", "decline_reason": error_text}, update_modified=True)
    elif job.target_doctype == "AOS Review":
        if frappe.db.exists("AOS Review", job.target_name):
            frappe.db.set_value("AOS Review", job.target_name, "status", "Pending", update_modified=True)
    elif job.target_doctype == "AOS Short":
        if frappe.db.exists("AOS Short", job.target_name):
            frappe.db.set_value(
                "AOS Short",
                job.target_name,
                {"visibility_status": "hidden", "approval_status": "flagged", "hidden_reason": error_text},
                update_modified=True,
            )


def _enqueue_ad_index(ad) -> None:
    try:
        from aos.integrations.ai.image_search_tasks import enqueue_index_refresh_for_status
        enqueue_index_refresh_for_status(ad.name, status=ad.status)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Failed to enqueue image-search refresh for {ad.name}")


def _notify_ad_approved(*, user: str, ad) -> None:
    try:
        from aos.services.notification_service import NotificationService
        NotificationService.notify_ad_approved(user=user, ad_id=ad.name, title=ad.title)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Ad moderation approved notification failed")


def _notify_ad_rejected(*, user: str, ad) -> None:
    try:
        from aos.services.notification_service import NotificationService
        NotificationService.notify_ad_rejected(user=user, ad_id=ad.name, title=ad.title)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Ad moderation rejected notification failed")


def enqueue_ad_moderation(ad_id: str, *, source: str = "ad_create") -> object | None:
    ad = frappe.get_doc("AOS Ad", ad_id)
    text_items = [
        build_text_item("title", getattr(ad, "title", "")),
        build_text_item("description", getattr(ad, "description", "")),
    ]
    media_items: list[dict[str, Any]] = []
    for row in getattr(ad, "images", []) or []:
        item = build_media_item(getattr(row, "media", ""), field="image")
        if item:
            media_items.append(item)
    if getattr(ad, "video_media", None):
        item = build_media_item(ad.video_media, field="video")
        if item:
            media_items.append(item)
    seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user") or ad.seller
    return create_moderation_job(
        target_doctype="AOS Ad",
        target_name=ad.name,
        target_owner=seller_user,
        content_kind="ad",
        source=source,
        text_items=text_items,
        media_items=media_items,
        context={"initial_status": ad.status},
        enqueue=True,
    )


def enqueue_review_moderation(review_id: str, *, source: str = "review_create") -> object | None:
    review = frappe.get_doc("AOS Review", review_id)
    text_items = [
        build_text_item("title", getattr(review, "title", "")),
        build_text_item("comment", getattr(review, "comment", "")),
    ]
    media_items: list[dict[str, Any]] = []
    for row in getattr(review, "review_images", []) or []:
        item = build_media_item(getattr(row, "media", ""), field="image")
        if item:
            media_items.append(item)
    return create_moderation_job(
        target_doctype="AOS Review",
        target_name=review.name,
        target_owner=review.reviewer,
        content_kind="review",
        source=source,
        text_items=text_items,
        media_items=media_items,
        context={"ad": review.ad, "rating": review.rating},
        enqueue=True,
    )


def enqueue_short_moderation(short_id: str, *, source: str = "short_publish", was_visible: bool = False) -> object | None:
    short = frappe.get_doc("AOS Short", short_id)
    text_items = [
        build_text_item("caption", getattr(short, "caption", "")),
        build_text_item("hashtags", getattr(short, "hashtags", ""), content_type="application/json"),
    ]
    media_items: list[dict[str, Any]] = []
    if getattr(short, "thumbnail_media", None):
        item = build_media_item(short.thumbnail_media, field="thumbnail")
        if item:
            media_items.append(item)
    if getattr(short, "raw_video_media", None):
        item = build_media_item(short.raw_video_media, field="raw_video")
        if item:
            media_items.append(item)
    return create_moderation_job(
        target_doctype="AOS Short",
        target_name=short.name,
        target_owner=short.owner,
        content_kind="short",
        source=source,
        text_items=text_items,
        media_items=media_items,
        context={"was_visible": bool(was_visible), "content_mode": short.content_mode},
        enqueue=True,
    )
