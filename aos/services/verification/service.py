"""Application service for Verification submission and owner retrieval."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.media.media_service import MediaService

from .constants import (
    RESUBMIT_FROM_STATUSES,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REVIEWING,
    TYPE_BUSINESS,
    TYPE_INDIVIDUAL,
    VERIFICATION_DOCUMENT_FIELD,
    VERIFICATION_DOCUMENT_PURPOSE,
    VERIFICATION_DOCTYPE,
)
from .evidence import validate_evidence_media
from .errors import VerificationConflictError, VerificationNotFoundError
from .observability import verification_log
from .policy import assert_submission_eligible
from .repository import get_request_for_user, lock_request_for_user
from .serializers import serialize_request
from .validation import normalize_submit_payload


class VerificationService:
    def __init__(self, media: MediaService | None = None):
        self.media = media or MediaService()

    def submit(self, *, user: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_submit_payload(payload, require_idempotency=True)
        assert_submission_eligible(user=user)

        # The Accounts profile lock serializes first-insert and resubmission races
        # for this account across all web workers/nodes.
        existing = lock_request_for_user(user)
        key_hash = self._hash_text(request["idempotency_key"])
        payload_hash = self._payload_hash(request)

        if existing:
            replay = self._resolve_existing(
                existing,
                key_hash=key_hash,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
            verification = existing
            old_media_ids = [str(row.media) for row in verification.verification_documents if row.media]
            verification.flags.aos_verification_action = "resubmit"
            verification.status = STATUS_PENDING
            verification.rejection_reason = None
            verification.verified_by = None
            verification.verified_on = None
            verification.set(VERIFICATION_DOCUMENT_FIELD, [])
            event = "verification.resubmitted"
        else:
            verification = frappe.new_doc(VERIFICATION_DOCTYPE)
            verification.flags.aos_verification_action = "submit"
            verification.user = user
            verification.status = STATUS_PENDING
            old_media_ids = []
            event = "verification.submitted"

        normalized_documents = self._validate_media_rows(
            user=user,
            rows=request["verification_documents"],
            verification_name=existing.name if existing else None,
        )
        self._apply_fields(verification, request)
        verification.submitted_on = now_datetime()
        verification.submission_idempotency_key_hash = key_hash
        verification.submission_payload_hash = payload_hash
        for row in normalized_documents:
            verification.append(
                VERIFICATION_DOCUMENT_FIELD,
                {
                    "document_type": row["document_type"],
                    "document_number": row["document_number"],
                    "issue_date": row["issue_date"],
                    "expiry_date": row["expiry_date"],
                    "media": row["media_id"],
                },
            )

        verification.save(ignore_permissions=True)
        self._replace_evidence(
            user=user,
            verification_name=verification.name,
            old_media_ids=old_media_ids,
            new_rows=normalized_documents,
        )
        verification_log(
            event,
            user=user,
            verification_id=verification.name,
            status=verification.status,
            count=len(normalized_documents),
        )
        return self._serialize_submission(verification)

    def get_my(self, *, user: str) -> dict[str, Any]:
        # The profile must exist because Accounts is the identity authority, but
        # Verification status itself is derived from the canonical request row.
        if not frappe.db.exists("AOS Profile", {"user": user}):
            raise VerificationNotFoundError("Profile not found.", code="PROFILE_NOT_FOUND")
        verification = get_request_for_user(user)
        return {
            "is_verified": bool(verification and verification.status == STATUS_APPROVED),
            "verification": serialize_request(verification) if verification else None,
        }

    def _resolve_existing(self, existing, *, key_hash: str, payload_hash: str) -> dict[str, Any] | None:
        stored_key = str(getattr(existing, "submission_idempotency_key_hash", "") or "")
        stored_payload = str(getattr(existing, "submission_payload_hash", "") or "")
        if stored_key and stored_key == key_hash:
            if stored_payload != payload_hash:
                raise VerificationConflictError(
                    "Idempotency key was already used with a different verification request.",
                    code="VERIFICATION_IDEMPOTENCY_CONFLICT",
                )
            return self._serialize_submission(existing)

        if existing.status in {STATUS_PENDING, STATUS_REVIEWING}:
            raise VerificationConflictError(
                "Verification request already in progress.", code="VERIFICATION_IN_PROGRESS"
            )
        if existing.status == STATUS_APPROVED:
            raise VerificationConflictError(
                "Account is already verified.", code="VERIFICATION_ALREADY_APPROVED"
            )
        if existing.status not in RESUBMIT_FROM_STATUSES:
            raise VerificationConflictError(
                "Verification request cannot be submitted in its current state.",
                code="VERIFICATION_INVALID_STATE",
            )
        return None

    def _validate_media_rows(
        self,
        *,
        user: str,
        rows: list[dict[str, Any]],
        verification_name: str | None,
    ) -> list[dict[str, Any]]:
        validated: list[dict[str, Any]] = []
        for row in rows:
            media_doc = validate_evidence_media(
                media=self.media,
                media_id=row["media_id"],
                user=user,
                verification_name=verification_name,
            )
            validated.append({**row, "media_id": media_doc.name})
        return validated

    def _replace_evidence(
        self,
        *,
        user: str,
        verification_name: str,
        old_media_ids: list[str],
        new_rows: list[dict[str, Any]],
    ) -> None:
        new_ids = {row["media_id"] for row in new_rows}
        for old_media_id in old_media_ids:
            if old_media_id in new_ids:
                continue
            self.media.release_media(
                media_id=old_media_id,
                user=user,
                attached_doctype=VERIFICATION_DOCTYPE,
                attached_name=verification_name,
            )
            verification_log(
                "verification.document.released",
                user=user,
                verification_id=verification_name,
            )

        for row in new_rows:
            self.media.attach_media(
                media_id=row["media_id"],
                user=user,
                purpose=VERIFICATION_DOCUMENT_PURPOSE,
                attached_doctype=VERIFICATION_DOCTYPE,
                attached_name=verification_name,
                attached_field=VERIFICATION_DOCUMENT_FIELD,
            )

    @staticmethod
    def _serialize_submission(verification) -> dict[str, Any]:
        return {
            "verification_id": verification.name,
            "verification_type": verification.verification_type,
            "status": verification.status,
            "submitted_on": getattr(verification, "submitted_on", None),
            "documents": [
                {
                    "document_type": row.document_type,
                    "media_id": row.media or None,
                }
                for row in (verification.verification_documents or [])
            ],
        }

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @classmethod
    def _payload_hash(cls, request: dict[str, Any]) -> str:
        canonical = {key: value for key, value in request.items() if key != "idempotency_key"}
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
        return cls._hash_text(encoded)

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
            verification.business_website = None
            verification.business_address = None
            return

        if request["verification_type"] == TYPE_BUSINESS:
            verification.business_name = request["business_name"]
            verification.business_type = request["business_type"]
            verification.business_category = request["business_category"]
            verification.business_phone_number = request["business_phone_number"]
            verification.business_email = request["business_email"]
            verification.business_website = request.get("business_website") or None
            verification.business_address = request["business_address"]
            verification.legal_name = None
            verification.phone_number = None
