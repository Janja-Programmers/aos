"""Staff-only manual review coordination.

Moderation owns the case/audit decision; each feature still owns its business
state transition. The case row is locked first so two reviewers and automatic
callbacks cannot both become authoritative.
"""
from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.utils.doctype_permissions import has_doctype_permission


class ModerationReviewError(RuntimeError):
    pass


def _clean_reason(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:1000]


def _require_reviewer(user: str) -> str:
    user = str(user or "").strip()
    if user in {"", "Guest"} or not has_doctype_permission(
        user=user,
        doctype="AOS Moderation Job",
        ptype="write",
    ):
        raise frappe.PermissionError("Manual moderation review is not allowed.")
    return user


def _lock_job(job_id: str):
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ModerationReviewError("Moderation case is required.")
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Moderation Job` WHERE name=%s LIMIT 1 FOR UPDATE",
        (job_id,),
        as_dict=True,
    )
    if not rows:
        raise ModerationReviewError("Moderation case not found.")
    return frappe.get_doc("AOS Moderation Job", rows[0].name)


def _apply_feature_decision(job, *, decision: str, reason: str, reviewer: str) -> None:
    if not frappe.db.exists(job.target_doctype, job.target_name):
        raise ModerationReviewError("Moderation target no longer exists.")

    if job.target_doctype == "AOS Ad":
        from aos.services.ads.review import review_ad

        doc = frappe.get_doc("AOS Ad", job.target_name)
        review_ad(
            public_id=doc.public_id,
            decision=decision,
            reason=reason,
            version=str(doc.modified or ""),
            reviewer=reviewer,
        )
        return

    if job.target_doctype == "AOS Review":
        from aos.services.reviews.moderation import review_review

        doc = frappe.get_doc("AOS Review", job.target_name)
        review_review(
            review_id=doc.public_id,
            decision=decision,
            reason=reason,
            version=str(doc.modified or ""),
            reviewer=reviewer,
        )
        return

    if job.target_doctype == "AOS Short":
        from aos.services.shorts.moderation import review_short

        doc = frappe.get_doc("AOS Short", job.target_name)
        review_short(
            short_id=doc.name,
            decision=decision,
            reason=reason,
            version=str(doc.modified or ""),
            reviewer=reviewer,
        )
        return

    raise ModerationReviewError(f"Unsupported moderation target: {job.target_doctype}")


def review_moderation_case(
    *,
    job_id: Any,
    decision: Any,
    reason: Any,
    version: Any,
    reviewer: str,
):
    reviewer = _require_reviewer(reviewer)
    clean_decision = str(decision or "").strip().lower()
    if clean_decision not in {"approve", "reject"}:
        raise ModerationReviewError("Invalid moderation decision.")
    clean_reason = _clean_reason(reason)
    if clean_decision == "reject" and not clean_reason:
        raise ModerationReviewError("A rejection reason is required.")
    if clean_decision == "approve" and clean_reason:
        raise ModerationReviewError("A rejection reason is only valid when rejecting.")

    job = _lock_job(str(job_id or ""))
    expected_version = str(version or "").strip()
    if expected_version and expected_version != str(job.modified or ""):
        raise ModerationReviewError("Moderation case changed since it was opened. Reload it.")
    if job.status != "Review Required" or job.decision_source == "manual":
        raise ModerationReviewError("Moderation case is no longer awaiting human review.")

    _apply_feature_decision(
        job,
        decision=clean_decision,
        reason=clean_reason,
        reviewer=reviewer,
    )

    # The feature transition above is authoritative for feature state. The same
    # transaction now closes the locked moderation case as the human audit row.
    job.decision = "allow" if clean_decision == "approve" else "reject"
    job.status = "Allowed" if clean_decision == "approve" else "Rejected"
    job.decision_source = "manual"
    job.decided_by = reviewer
    job.decided_at = now_datetime()
    job.completed_at = job.completed_at or now_datetime()
    if clean_reason:
        job.reasons_json = frappe.as_json([clean_reason])
    job.save(ignore_permissions=True)
    return job
