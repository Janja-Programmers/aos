"""Application service for canonical User, Ad, Short, and Review reports."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.accounts.identity import normalize_public_account_id, resolve_account_reference
from aos.services.ads.errors import AdsNotFoundError
from aos.services.ads.visibility import require_public_ad_for_viewer
from aos.services.shorts.identity import normalize_short_id
from aos.services.shorts.policy import can_view as can_view_short

from .constants import (
    AD_REPORT_FIELDS,
    DETAIL_MAX_LENGTH,
    REPORT_TARGET_AD,
    REPORT_TARGET_REVIEW,
    REPORT_TARGET_SHORT,
    REPORT_TARGET_USER,
    REVIEW_REPORT_FIELDS,
    SHORT_REPORT_FIELDS,
    STATUS_REVIEWING,
    USER_REPORT_FIELDS,
)
from .errors import ReportConflictError, ReportNotFoundError, ReportSelfError
from .observability import report_log
from .policy import require_reportable_user
from .repository import find_reviewing_report
from .validation import (
    ensure_known_fields,
    normalize_details,
    strip_transport_fields,
    validate_reason_for_target,
)


class ReportService:
    """Report use-cases; the API boundary owns savepoint/rollback semantics."""

    def report_user(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, USER_REPORT_FIELDS)
        account_id = normalize_public_account_id(request.get("account_id"))
        if not account_id:
            raise ReportNotFoundError("Account not found.")
        target_user = resolve_account_reference(account_id) or ""
        require_reportable_user(target_user=target_user, reporter=user)

        reason = validate_reason_for_target(request.get("reason_id"), REPORT_TARGET_USER)
        details = normalize_details(
            request.get("details"), max_length=DETAIL_MAX_LENGTH["AOS User Report"]
        )
        return self._create_or_replay(
            doctype="AOS User Report",
            reporter=user,
            target_field="reported_user",
            target_internal_id=target_user,
            target_public_id=account_id,
            report_type="user",
            reason=reason,
            details=details,
        )

    def report_ad(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, AD_REPORT_FIELDS)
        ad_id = str(request.get("ad_id") or "").strip()
        if not ad_id or len(ad_id) > 140:
            raise ReportNotFoundError("Ad not found.")

        try:
            visible = require_public_ad_for_viewer(public_id=ad_id, viewer=user)
        except AdsNotFoundError as exc:
            raise ReportNotFoundError("Ad not found.") from exc
        if str(visible.seller_user or "") == user:
            raise ReportSelfError("You cannot report your own ad.")

        reason = validate_reason_for_target(request.get("reason_id"), REPORT_TARGET_AD)
        details = normalize_details(
            request.get("details"), max_length=DETAIL_MAX_LENGTH["AOS Ad Report"]
        )
        return self._create_or_replay(
            doctype="AOS Ad Report",
            reporter=user,
            target_field="ad",
            target_internal_id=str(visible.name),
            target_public_id=str(visible.public_id),
            report_type="ad",
            reason=reason,
            details=details,
            extra_fields={"seller": str(visible.seller)},
        )

    def report_short(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, SHORT_REPORT_FIELDS)
        short_id = normalize_short_id(request.get("short_id"))
        if not short_id:
            raise ReportNotFoundError("Short not found.")
        short = frappe.db.get_value(
            "AOS Short",
            short_id,
            ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience"],
            as_dict=True,
        )
        if not short or not can_view_short(short, viewer=user):
            raise ReportNotFoundError("Short not found.")
        if str(short.owner or "") == user:
            raise ReportSelfError("You cannot report your own short.")

        reason = validate_reason_for_target(request.get("reason_id"), REPORT_TARGET_SHORT)
        details = normalize_details(
            request.get("details"), max_length=DETAIL_MAX_LENGTH["AOS Short Report"]
        )
        return self._create_or_replay(
            doctype="AOS Short Report",
            reporter=user,
            target_field="short",
            target_internal_id=short_id,
            target_public_id=short_id,
            report_type="short",
            reason=reason,
            details=details,
            extra_fields={"short_owner": str(short.owner)},
        )

    def report_review(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, REVIEW_REPORT_FIELDS)
        review_id = str(request.get("review_id") or "").strip()
        if not review_id or len(review_id) > 140:
            raise ReportNotFoundError("Review not found.")

        from aos.services.reviews.errors import ReviewNotFoundError as ReviewTargetNotFoundError
        from aos.services.reviews.ids import resolve_review_name
        from aos.services.reviews.service import ReviewService

        try:
            ReviewService().get(payload={"review_id": review_id}, viewer=user)
            review_name = resolve_review_name(review_id)
        except ReviewTargetNotFoundError as exc:
            raise ReportNotFoundError("Review not found.") from exc

        review = frappe.db.get_value(
            "AOS Review",
            review_name,
            ["name", "public_id", "reviewer", "status"],
            as_dict=True,
        )
        if not review or str(review.status or "") != "Approved":
            raise ReportNotFoundError("Review not found.")
        if str(review.reviewer or "") == user:
            raise ReportSelfError("You cannot report your own review.")

        reason = validate_reason_for_target(request.get("reason_id"), REPORT_TARGET_REVIEW)
        details = normalize_details(
            request.get("details"), max_length=DETAIL_MAX_LENGTH["AOS Review Report"]
        )
        return self._create_or_replay(
            doctype="AOS Review Report",
            reporter=user,
            target_field="review",
            target_internal_id=str(review.name),
            target_public_id=str(review.public_id),
            report_type="review",
            reason=reason,
            details=details,
            extra_fields={"review_owner": str(review.reviewer)},
        )

    @staticmethod
    def _create_or_replay(
        *,
        doctype: str,
        reporter: str,
        target_field: str,
        target_internal_id: str,
        target_public_id: str,
        report_type: str,
        reason: str,
        details: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = find_reviewing_report(
            doctype=doctype,
            target_id=target_internal_id,
            reporter=reporter,
        )
        if existing:
            return ReportService._projection(
                row=existing,
                report_type=report_type,
                target_id=target_public_id,
                idempotent_replay=True,
            )

        values: dict[str, Any] = {
            "doctype": doctype,
            target_field: target_internal_id,
            "reported_by": reporter,
            "reason": reason,
            "details": details,
            "status": STATUS_REVIEWING,
            **(extra_fields or {}),
        }
        doc = frappe.get_doc(values)
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            existing = find_reviewing_report(
                doctype=doctype,
                target_id=target_internal_id,
                reporter=reporter,
                lock=True,
            )
            if not existing:
                raise ReportConflictError(
                    "Report submission could not be reconciled."
                ) from exc
            return ReportService._projection(
                row=existing,
                report_type=report_type,
                target_id=target_public_id,
                idempotent_replay=True,
            )

        report_log("report.submitted", report_id=doc.name, doctype=doc.doctype, status=doc.status)
        return {
            "report_id": str(doc.name),
            "report_type": report_type,
            "target_id": target_public_id,
            "reason_id": str(doc.reason),
            "status": str(doc.status),
            "created_at": str(doc.creation or ""),
            "idempotent_replay": False,
        }

    @staticmethod
    def _projection(*, row, report_type: str, target_id: str, idempotent_replay: bool) -> dict[str, Any]:
        return {
            "report_id": str(row.name),
            "report_type": report_type,
            "target_id": target_id,
            "reason_id": str(row.reason),
            "status": str(row.status),
            "created_at": str(row.creation or ""),
            "idempotent_replay": bool(idempotent_replay),
        }
