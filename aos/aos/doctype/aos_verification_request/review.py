"""Frappe Desk transport for Verification reviewer actions."""

from __future__ import annotations

import frappe

from aos.services.verification.errors import (
    VerificationError,
    VerificationNotFoundError,
    VerificationPermissionError,
)
from aos.services.verification.review import review_verification_request as apply_review_action


@frappe.whitelist(methods=["POST"])
def review_verification_request(name=None, action=None, reason=None, version=None):
    """Apply one permission-protected Verification review action from Desk."""
    reviewer = str(getattr(frappe.session, "user", "") or "").strip()
    try:
        doc = apply_review_action(
            request_name=name,
            action=action,
            reason=reason,
            version=version,
            reviewer=reviewer,
        )
    except VerificationPermissionError as exc:
        frappe.throw(str(exc), frappe.PermissionError)
    except VerificationNotFoundError as exc:
        frappe.throw(str(exc), frappe.DoesNotExistError)
    except VerificationError as exc:
        frappe.throw(str(exc), frappe.ValidationError)

    return {
        "verification_id": doc.name,
        "status": doc.status,
        "version": str(doc.modified or ""),
        "rejection_reason": doc.rejection_reason or None,
    }
