"""Operator-only cleanup for synthetic fixtures leaked by older AOS test revisions.

This module is never scheduled and is not a client API.  It exists so staging can
remove test rows created by pre-isolation test code after deploying the corrected
suite.  Deletion requires an explicit confirmation token and only targets patterns
owned by AOS tests (unique-* users, test-* durable jobs, and test-private Shorts
media).
"""
from __future__ import annotations

from typing import Any

import frappe

_CONFIRM = "DELETE_AOS_TEST_ARTIFACTS"
_JOB_DOCTYPES = (
    "AOS Video Processing Job",
    "AOS Moderation Job",
    "AOS Search Index Job",
    "AOS Notification Delivery Job",
    "AOS Analytics Ingest Job",
)
_SHORT_RELATIONS = (
    "AOS Short Moderation Decision",
    "AOS Short Metrics Daily",
    "AOS Short Event",
    "AOS Short View",
    "AOS Short Feedback",
    "AOS Short Repost",
    "AOS Short Save",
    "AOS Short Like",
    "AOS Short Mention",
    "AOS Short Comment",
    "AOS Short Sound",
    "AOS Short Ad",
    "AOS Short Hashtag",
    "AOS Short Mode",
    "AOS Short Photo",
    "AOS Short Report",
)


def _names(doctype: str, filters: dict[str, Any]) -> list[str]:
    if not frappe.db.exists("DocType", doctype):
        return []
    return [str(value) for value in frappe.get_all(doctype, filters=filters, pluck="name", limit=0)]


def cleanup_leaked_test_artifacts(confirm: str | None = None) -> dict[str, Any]:
    """Preview or delete only known synthetic test artifacts.

    Call without ``confirm`` for a dry-run count.  Destructive cleanup requires
    ``confirm='DELETE_AOS_TEST_ARTIFACTS'``.
    """
    frappe.set_user("Administrator")
    # Never let whatever transaction invoked this maintenance function become
    # part of the cleanup commit.
    frappe.db.rollback()

    users = _names("User", {"name": ["like", "unique-%@example.com"]})
    media = _names(
        "AOS Media Object",
        {
            "owner_user": "Administrator",
            "bucket": "test-private",
            "object_key": ["like", "tests/%/raw.mp4"],
        },
    )
    shorts = _names("AOS Short", {"raw_video_media": ["in", media]}) if media else []

    jobs: dict[str, list[str]] = {
        doctype: _names(doctype, {"idempotency_key": ["like", "test-%"]})
        for doctype in _JOB_DOCTYPES
    }
    summary = {
        "dry_run": confirm != _CONFIRM,
        "users": len(users),
        "shorts": len(shorts),
        "media": len(media),
        "jobs": {doctype: len(names) for doctype, names in jobs.items()},
    }
    if confirm != _CONFIRM:
        return summary

    # Durable outbox rows must go before their jobs/domains.
    if frappe.db.exists("DocType", "AOS Transactional Outbox"):
        for doctype, names in jobs.items():
            if names:
                frappe.db.sql(
                    "DELETE FROM `tabAOS Transactional Outbox` WHERE job_doctype=%s AND job_name IN %s",
                    (doctype, tuple(names)),
                )
        if shorts:
            frappe.db.sql(
                "DELETE FROM `tabAOS Transactional Outbox` "
                "WHERE aggregate_doctype='AOS Short' AND aggregate_name IN %s",
                (tuple(shorts),),
            )

    for doctype, names in jobs.items():
        if names:
            frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE name IN %s", (tuple(names),))

    if shorts:
        short_tuple = tuple(shorts)
        comments = _names("AOS Short Comment", {"short": ["in", short_tuple]})
        if comments:
            frappe.db.sql("DELETE FROM `tabAOS Short Comment Like` WHERE comment IN %s", (tuple(comments),))
        sounds = _names("AOS Sound", {"created_from_short": ["in", short_tuple]})
        for doctype in _SHORT_RELATIONS:
            frappe.db.sql(f"DELETE FROM `tab{doctype}` WHERE short IN %s", (short_tuple,))
        if sounds:
            frappe.db.sql("DELETE FROM `tabAOS Sound Favorite` WHERE sound IN %s", (tuple(sounds),))
            frappe.db.sql(
                "DELETE FROM `tabAOS Media Object` WHERE attached_doctype='AOS Sound' AND attached_name IN %s",
                (tuple(sounds),),
            )
            frappe.db.sql("DELETE FROM `tabAOS Sound` WHERE name IN %s", (tuple(sounds),))
        frappe.db.sql(
            "DELETE FROM `tabAOS Media Object` WHERE attached_doctype='AOS Short' AND attached_name IN %s",
            (short_tuple,),
        )
        frappe.db.sql("DELETE FROM `tabAOS Short` WHERE name IN %s", (short_tuple,))

    # Notification-delivery committed fixtures use these exact synthetic markers.
    if frappe.db.exists("DocType", "AOS Push Token"):
        frappe.db.sql(
            "DELETE FROM `tabAOS Push Token` WHERE user='Administrator' "
            "AND (token LIKE 'test-token-%' OR device_id LIKE 'test-%')"
        )
    if frappe.db.exists("DocType", "AOS Notification"):
        frappe.db.sql(
            "DELETE FROM `tabAOS Notification` WHERE user='Administrator' "
            "AND dedupe_key LIKE 'test:notification-delivery:%'"
        )

    if media:
        frappe.db.sql("DELETE FROM `tabAOS Media Object` WHERE name IN %s", (tuple(media),))

    # The uniqueness suite used unique-<random>-*@example.com. Remove only that
    # synthetic namespace; normal example.com accounts are deliberately untouched.
    if users:
        user_tuple = tuple(users)
        for table, predicate in (
            ("AOS Short Comment Like", "user IN %s"),
            ("AOS Short Comment", "user IN %s"),
            ("AOS Short Like", "user IN %s"),
            ("AOS Short Save", "user IN %s"),
            ("AOS Short Repost", "user IN %s"),
            ("AOS Short Feedback", "user IN %s"),
            ("AOS Short View", "user IN %s"),
            ("AOS Short Event", "user IN %s"),
            ("AOS User Activity", "user IN %s"),
            ("AOS Push Token", "user IN %s"),
            ("AOS User Preference", "user IN %s"),
            ("AOS Profile", "user IN %s"),
        ):
            if frappe.db.exists("DocType", table):
                frappe.db.sql(f"DELETE FROM `tab{table}` WHERE {predicate}", (user_tuple,))
        if frappe.db.exists("DocType", "AOS User Block"):
            frappe.db.sql(
                "DELETE FROM `tabAOS User Block` WHERE blocker_user IN %s OR blocked_user IN %s",
                (user_tuple, user_tuple),
            )
        if frappe.db.exists("DocType", "AOS Live Stream View"):
            frappe.db.sql("DELETE FROM `tabAOS Live Stream View` WHERE user IN %s", (user_tuple,))
        if frappe.db.exists("DocType", "AOS Live Stream"):
            frappe.db.sql("DELETE FROM `tabAOS Live Stream` WHERE host_user IN %s", (user_tuple,))
        for user in users:
            if frappe.db.exists("User", user):
                frappe.delete_doc("User", user, force=1, ignore_permissions=True)

    frappe.db.commit()
    return {**summary, "dry_run": False, "deleted": True}
