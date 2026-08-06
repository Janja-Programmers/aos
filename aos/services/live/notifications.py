"""Bounded, idempotent Live notification fanout."""

from __future__ import annotations

import hashlib

import frappe

from aos.services.notification_service import NotificationService
from aos.services.social.constants import MAX_SOCIAL_EVENT_FANOUT
from aos.services.social.repository import SocialRepository

from .observability import live_log

FANOUT_BATCH = 100


def _dedupe_key(*, user: str, live_id: str) -> str:
    material = f"live_started|{live_id}|{user}".encode("utf-8")
    return "live:" + hashlib.sha256(material).hexdigest()


def enqueue_live_started_fanout(live_id: str) -> None:
    try:
        frappe.enqueue(
            "aos.tasks.live.fanout_live_started_notifications",
            queue="short",
            enqueue_after_commit=True,
            live_id=live_id,
            delivered=0,
        )
    except Exception:
        live_log("notification_fanout_enqueue", outcome="failure", reason="dependency")


def _already_notified(*, user: str, host_user: str, live_id: str) -> bool:
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Notification`
        WHERE dedupe_key=%(dedupe_key)s
           OR (user=%(user)s AND actor=%(host_user)s AND type='live_started'
               AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.live_id'))=%(live_id)s)
        LIMIT 1
        """,
        {
            "dedupe_key": _dedupe_key(user=user, live_id=live_id),
            "user": user,
            "host_user": host_user,
            "live_id": live_id,
        },
    )
    return bool(rows)


def deliver_live_started_fanout(
    *,
    live_id: str,
    after_creation: str | None = None,
    after_name: str | None = None,
    delivered: int = 0,
) -> dict[str, object]:
    live = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["host_user", "title", "status", "is_active"],
        as_dict=True,
    )
    if not live or live.status != "live" or not bool(live.is_active):
        return {"ok": True, "outcome": "inactive", "delivered": int(delivered or 0)}

    delivered = max(0, int(delivered or 0))
    remaining = max(0, int(MAX_SOCIAL_EVENT_FANOUT) - delivered)
    if not remaining:
        return {"ok": True, "outcome": "fanout_cap", "delivered": delivered}

    page_size = min(FANOUT_BATCH, remaining)
    rows = SocialRepository().list_active_followers_for_event_page(
        target=live.host_user,
        limit=page_size + 1,
        after_creation=after_creation,
        after_name=after_name,
    )
    page = rows[:page_size]
    created = 0
    for row in page:
        try:
            if _already_notified(user=row["user"], host_user=live.host_user, live_id=live_id):
                continue
            NotificationService.notify_live_started(
                user=row["user"],
                host_user=live.host_user,
                live_id=live_id,
                title=live.title,
                dedupe_key=_dedupe_key(user=row["user"], live_id=live_id),
            )
            created += 1
        except Exception:
            live_log("notification_fanout_item", outcome="failure", reason="internal")

    total_scanned = delivered + len(page)
    has_more = len(rows) > page_size and total_scanned < int(MAX_SOCIAL_EVENT_FANOUT)
    if has_more and page:
        last = page[-1]
        try:
            frappe.enqueue(
                "aos.tasks.live.fanout_live_started_notifications",
                queue="short",
                enqueue_after_commit=True,
                live_id=live_id,
                after_creation=last["creation"],
                after_name=last["name"],
                delivered=total_scanned,
            )
        except Exception:
            live_log("notification_fanout_enqueue", outcome="failure", reason="dependency")
    live_log(
        "notification_fanout_batch",
        outcome="success",
        reason="none",
        count=created,
    )
    return {
        "ok": True,
        "outcome": "continued" if has_more else "complete",
        "delivered": total_scanned,
        "created": created,
    }
