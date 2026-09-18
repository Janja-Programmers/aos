"""Frappe Desk transport for manual Review moderation."""

from __future__ import annotations

import frappe

from aos.services.reviews.errors import ReviewError, ReviewNotFoundError, ReviewPermissionError
from aos.services.reviews.moderation import review_review as apply_review_decision


@frappe.whitelist(methods=["POST"])
def review_review(review_id=None, decision=None, reason=None, version=None):
    reviewer = str(getattr(frappe.session, "user", "") or "").strip()
    try:
        doc = apply_review_decision(
            review_id=review_id,
            decision=decision,
            reason=reason,
            version=version,
            reviewer=reviewer,
        )
    except ReviewPermissionError as exc:
        frappe.throw(str(exc), frappe.PermissionError)
    except ReviewNotFoundError as exc:
        frappe.throw(str(exc), frappe.DoesNotExistError)
    except ReviewError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    return {
        "review_id": doc.public_id,
        "status": doc.status,
        "version": str(doc.modified or ""),
    }
