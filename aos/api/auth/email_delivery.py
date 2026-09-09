"""Low-latency, durable delivery kick for authentication emails.

Auth OTPs are first persisted in Frappe's Email Queue inside the surrounding
request transaction. After that transaction commits, AOS best-effort enqueues a
short RQ job for the specific Email Queue row so security emails do not wait for
Frappe's periodic bulk email flush. The normal Frappe scheduler remains the
fallback if Redis/RQ is temporarily unavailable.
"""

from __future__ import annotations

import frappe

from .observability import log_auth_exception

AUTH_EMAIL_QUEUE = "short"
AUTH_EMAIL_TIMEOUT_SECONDS = 120


def _clean_queue_name(value: str) -> str:
    return str(value or "").strip()[:140]


def _enqueue_after_commit(email_queue_name: str) -> None:
    """Best-effort enqueue after DB commit; scheduler remains the fallback."""
    name = _clean_queue_name(email_queue_name)
    if not name:
        return
    try:
        frappe.enqueue(
            "aos.api.auth.email_delivery.send_auth_email_queue",
            queue=AUTH_EMAIL_QUEUE,
            timeout=AUTH_EMAIL_TIMEOUT_SECONDS,
            job_id=f"aos_auth_email:{frappe.local.site}:{name}",
            email_queue_name=name,
        )
    except Exception as exc:
        # Do not turn a successful committed auth mutation into a client-visible
        # failure merely because the low-latency RQ kick is unavailable. The
        # Email Queue row is durable and Frappe's scheduled flush will retry it.
        log_auth_exception(
            "AOS Auth Email Dispatch Enqueue Failed",
            exc,
            operation="auth_email_dispatch_enqueue",
        )


def schedule_auth_email_delivery(email_queue_name: str) -> None:
    """Schedule low-latency delivery only after the Email Queue row commits."""
    name = _clean_queue_name(email_queue_name)
    if not name:
        return
    manager = getattr(frappe.db, "after_commit", None)
    if manager is None or not hasattr(manager, "add"):
        return
    manager.add(lambda name=name: _enqueue_after_commit(name))


def send_auth_email_queue(email_queue_name: str) -> None:
    """Send one committed auth Email Queue row, serialized against bulk flush."""
    name = _clean_queue_name(email_queue_name)
    if not name:
        return

    rows = frappe.db.sql(
        "SELECT name FROM `tabEmail Queue` WHERE name = %s FOR UPDATE",
        (name,),
        as_dict=True,
    )
    if not rows:
        return

    queue_doc = frappe.get_doc("Email Queue", name)
    if not queue_doc.is_to_be_sent():
        return

    try:
        queue_doc.send()
    except Exception as exc:
        log_auth_exception(
            "AOS Auth Email Delivery Failed",
            exc,
            operation="auth_email_delivery",
        )
        # Let RQ record the failed attempt. Frappe Email Queue state/retry
        # handling remains authoritative and the scheduled flush is fallback.
        raise
