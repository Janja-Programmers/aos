"""Shorts-owned manual Desk review boundary.

This module does not implement moderation analysis. It authorizes a human
reviewer, applies a decision to the current Short revision, advances the
moderation generation so stale automated callbacks cannot win, and records an
immutable audit row.
"""
from __future__ import annotations

import json
import frappe
from frappe.utils import now_datetime

from aos.services.media.media_service import MediaService
from aos.services.shorts.policy import technically_ready
from aos.services.shorts.serializers import serialize_owner_short
from aos.utils.doctype_permissions import has_doctype_permission


class ShortReviewError(RuntimeError):
    pass


def _reviewer(user: str | None) -> str:
    value = str(user or frappe.session.user or "").strip()
    if value in {"", "Guest"} or not has_doctype_permission(user=value, doctype="AOS Short", ptype="write"):
        raise ShortReviewError("Manual Short review is not allowed.")
    return value


def _lock(short_id: str):
    rows = frappe.db.sql("SELECT name FROM `tabAOS Short` WHERE name=%s LIMIT 1 FOR UPDATE", (str(short_id or "").strip(),), as_dict=True)
    if not rows:
        raise ShortReviewError("Short not found.")
    return frappe.get_doc("AOS Short", rows[0].name)


def _assert_version(doc, version: str | None) -> None:
    supplied = str(version or "").strip()
    if supplied and supplied != str(doc.modified or ""):
        raise ShortReviewError("Short changed since it was opened. Reload and review the current revision.")


def review_short(*, short_id: str, decision: str, reason: str | None, version: str | None, reviewer: str | None = None):
    reviewer = _reviewer(reviewer)
    clean_decision = str(decision or "").strip().lower()
    if clean_decision not in {"approve", "reject", "hide"}:
        raise ShortReviewError("Invalid review decision.")
    clean_reason = " ".join(str(reason or "").replace("\x00", "").split())[:1000]
    if clean_decision in {"reject", "hide"} and not clean_reason:
        raise ShortReviewError("A reason is required for reject/hide decisions.")

    doc = _lock(short_id)
    _assert_version(doc, version)
    if str(doc.lifecycle_status) == "Deleted":
        raise ShortReviewError("Deleted Short cannot be reviewed.")
    if clean_decision == "approve" and not technically_ready(doc):
        raise ShortReviewError("Short is not technically ready.")

    doc.moderation_generation = int(doc.moderation_generation or 0) + 1
    doc.moderation_decided_by = reviewer
    doc.moderation_decided_at = now_datetime()
    if clean_decision == "approve":
        doc.moderation_status = "Approved"
        doc.lifecycle_status = "Published"
        doc.moderation_reason = None
        doc.posted_on = doc.posted_on or now_datetime()
        audit_decision = "Approved"
    elif clean_decision == "reject":
        doc.moderation_status = "Rejected"
        doc.lifecycle_status = "Rejected"
        doc.moderation_reason = clean_reason
        audit_decision = "Rejected"
    else:
        doc.moderation_status = "Hidden"
        doc.lifecycle_status = "Hidden"
        doc.moderation_reason = clean_reason
        audit_decision = "Hidden"
    doc.save(ignore_permissions=True)

    frappe.get_doc({
        "doctype": "AOS Short Moderation Decision",
        "short": doc.name,
        "revision": int(doc.revision or 0),
        "generation": int(doc.moderation_generation or 0),
        "decision": audit_decision,
        "decision_source": "Manual",
        "reason": clean_reason,
        "decided_by": reviewer,
        "decided_at": now_datetime(),
        "automated_result": getattr(doc, "automated_moderation_result", None),
    }).insert(ignore_permissions=True)

    try:
        from aos.services.search_ranking_service import enqueue_short_search_index
        enqueue_short_search_index(doc.name, source=f"short_manual_{clean_decision}")
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Short search refresh failed: {doc.name}")
    try:
        from aos.services.notifications.service import NotificationService
        if clean_decision == "approve":
            NotificationService.notify_short_approved(user=doc.owner, short_id=doc.name, decision_token=str(doc.moderation_generation))
        else:
            NotificationService.notify_short_moderation_action(user=doc.owner, short_id=doc.name, action=clean_decision, reason=clean_reason, decision_token=str(doc.moderation_generation))
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Short moderation notification failed: {doc.name}")
    return doc


def review_context(*, short_id: str, reviewer: str | None = None) -> dict:
    reviewer = _reviewer(reviewer)
    doc = _lock(short_id)
    payload = serialize_owner_short(doc, viewer=reviewer)
    media = MediaService()
    raw_ids = []
    for key in ("raw_video_media", "playback_media", "poster_media", "cover_media", "storyboard_media"):
        value = str(getattr(doc, key, "") or "")
        if value:
            raw_ids.append(value)
    for row in frappe.get_all("AOS Short Photo", filters={"short": doc.name}, fields=["media"], order_by="position asc, name asc"):
        raw_ids.append(str(row.media))
    media_urls = {}
    for media_id in raw_ids:
        try:
            media_urls[media_id] = media.get_url(media_id=media_id, user=reviewer, expiry_minutes=10)
        except Exception:
            continue
    payload["review"] = {
        "current_revision": int(doc.revision or 0),
        "moderation_generation": int(doc.moderation_generation or 0),
        "automated_result": json.loads(doc.automated_moderation_result) if isinstance(doc.automated_moderation_result, str) and doc.automated_moderation_result else (doc.automated_moderation_result or None),
        "media_urls": media_urls,
        "mentions": [dict(row) for row in frappe.get_all("AOS Short Mention", filters={"short": doc.name}, fields=["mentioned_account","source_type","token"], order_by="creation asc")],
        "history": [dict(row) for row in frappe.get_all("AOS Short Moderation Decision", filters={"short": doc.name}, fields=["revision","generation","decision","reason","decided_by","decided_at"], order_by="decided_at desc, creation desc", limit=100)],
    }
    return payload
