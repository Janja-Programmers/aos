"""Application service for Verification submission and retrieval."""

from __future__ import annotations

from typing import Any

import hashlib
import frappe

from aos.services.media.media_service import MediaError, MediaService

from .constants import RESUBMIT_FROM_STATUSES, STATUS_PENDING, TYPE_BUSINESS, TYPE_INDIVIDUAL
from .errors import VerificationConflictError, VerificationValidationError
from .observability import verification_log
from .policy import assert_submission_eligible
from .repository import get_latest_request_for_user, lock_request_for_user
from .serializers import serialize_request
from .validation import normalize_submit_payload


class VerificationService:
    def __init__(self, media: MediaService | None = None):
        self.media = media or MediaService()

    def submit(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_submit_payload(payload)
        verification_type = request["verification_type"]
        assert_submission_eligible(user=user, verification_type=verification_type)

        # Profile FOR UPDATE in policy serializes submissions for this account,
        # including the first insert where no verification row exists yet.
        existing = lock_request_for_user(user)
        is_resubmit = existing is not None
        idempotency_hash = self._idempotency_hash(request.get("idempotency_key"))
        if existing:
            if existing.status in {"Pending", "Reviewing"}:
                stored_hash = str(getattr(existing, "submission_idempotency_hash", "") or "")
                if idempotency_hash and stored_hash and idempotency_hash == stored_hash:
                    return self._serialize_submission(existing)
                raise VerificationConflictError(
                    "Verification request already in progress.", code="VERIFICATION_IN_PROGRESS"
                )
            if existing.status not in RESUBMIT_FROM_STATUSES:
                raise VerificationConflictError(
                    "Verification request cannot be submitted in its current state.",
                    code="VERIFICATION_INVALID_STATE",
                )
            verification = existing
            old_media_ids = [str(row.media) for row in verification.verification_documents if row.media]
            verification.flags.aos_verification_action = "resubmit"
            verification.status = STATUS_PENDING
            verification.rejection_reason = None
            verification.verified_by = None
            verification.verified_on = None
            verification.set("verification_documents", [])
        else:
            verification = frappe.new_doc("AOS Verification Request")
            verification.user = user
            verification.status = STATUS_PENDING
            old_media_ids = []

        normalized_documents = self._validate_media(
            user=user, rows=request["verification_documents"], verification_name=existing.name if existing else None
        )
        self._apply_fields(verification, request)
        if hasattr(verification, "submission_idempotency_hash"):
            verification.submission_idempotency_hash = idempotency_hash or None
        for row in normalized_documents:
            verification.append(
                "verification_documents",
                {
                    "document_type": row["document_type"],
                    "document_number": row["document_number"],
                    "issue_date": row["issue_date"],
                    "expiry_date": row["expiry_date"],
                    "media": row["media_id"],
                    "attachment": "",
                },
            )

        verification.save(ignore_permissions=True)

        # Release old evidence before attaching new evidence so the existing
        # 10-items-per-request Media policy remains enforceable on resubmit.
        new_ids = {row["media_id"] for row in normalized_documents}
        for old_media_id in old_media_ids:
            if old_media_id in new_ids:
                continue
            self.media.release_media(
                media_id=old_media_id,
                user=user,
                attached_doctype="AOS Verification Request",
                attached_name=verification.name,
            )
            verification_log(
                "verification.document.released",
                user=user,
                verification_id=verification.name,
            )

        for row in normalized_documents:
            self.media.attach_media(
                media_id=row["media_id"],
                user=user,
                purpose="verification_document",
                attached_doctype="AOS Verification Request",
                attached_name=verification.name,
                attached_field="verification_documents",
            )
        verification_log(
            "verification.resubmitted" if is_resubmit else "verification.submitted",
            user=user,
            verification_id=verification.name,
            status=verification.status,
            count=len(normalized_documents),
        )
        return self._serialize_submission(verification)

    @staticmethod
    def _serialize_submission(verification) -> dict[str, Any]:
        return {
            "id": verification.name,
            "verification_type": verification.verification_type,
            "status": verification.status,
            "documents": [
                {
                    "document_type": row.document_type,
                    "media": row.media or None,
                    "media_id": row.media or None,
                }
                for row in (verification.verification_documents or [])
            ],
        }

    def get_my(self, *, user: str) -> dict[str, Any]:
        profile = frappe.db.get_value(
            "AOS Profile",
            user,
            ["is_verified", "verified_on"],
            as_dict=True,
        )
        if not profile:
            from .errors import VerificationNotFoundError

            raise VerificationNotFoundError("Profile not found.", code="PROFILE_NOT_FOUND")
        verification = get_latest_request_for_user(user)
        return {
            "is_verified": bool(profile.is_verified),
            "verified_on": profile.verified_on,
            "verification": serialize_request(verification) if verification else None,
        }

    def _validate_media(
        self, *, user: str, rows: list[dict[str, Any]], verification_name: str | None = None
    ) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        for row in rows:
            try:
                media_doc = self.media.validate_media_for_use(
                    media_id=row["media_id"],
                    user=user,
                    purpose="verification_document",
                    attached_doctype="AOS Verification Request" if verification_name else None,
                    attached_name=verification_name,
                )
            except MediaError as exc:
                # Do not leak whether an arbitrary Media ID exists, is owned by
                # another account, or is attached to another sensitive record.
                raise VerificationValidationError(
                    "Invalid verification document media.", code="VERIFICATION_INVALID_DOCUMENT"
                ) from exc
            if str(getattr(media_doc, "visibility", "")) != "Private":
                raise VerificationValidationError(
                    "Verification documents must be private.", code="VERIFICATION_INVALID_DOCUMENT"
                )
            validated.append({**row, "media_id": media_doc.name})
        return validated

    @staticmethod
    def _idempotency_hash(value: str | None) -> str:
        clean = str(value or "").strip()
        return hashlib.sha256(clean.encode("utf-8")).hexdigest() if clean else ""

    @staticmethod
    def _apply_fields(verification, request: dict[str, Any]) -> None:
        verification.verification_type = request["verification_type"]
        if request["verification_type"] == TYPE_INDIVIDUAL:
            verification.legal_name = request["legal_name"]
            verification.phone_number = request["phone_number"]
            verification.business_name = None
            verification.business_type = None
            verification.business_category = None
            verification.business_phone_number = None
            verification.business_email = None
            if hasattr(verification, "business_website"):
                verification.business_website = None
            verification.business_address = None
            return

        if request["verification_type"] == TYPE_BUSINESS:
            verification.business_name = request["business_name"]
            verification.business_type = request["business_type"]
            verification.business_category = request["business_category"]
            verification.business_phone_number = request["business_phone_number"]
            verification.business_email = request["business_email"]
            if hasattr(verification, "business_website"):
                verification.business_website = request.get("business_website") or None
            verification.business_address = request["business_address"]
            verification.legal_name = None
            verification.phone_number = None
