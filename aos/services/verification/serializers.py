"""Privacy-safe serializers for Verification APIs."""

from __future__ import annotations

from typing import Any

from aos.services.accounts.identity import public_account_id_for_user


def mask_document_number(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * min(8, len(text) - 4)}{text[-4:]}"


def serialize_document(row) -> dict[str, Any]:
    return {
        "document_type": str(getattr(row, "document_type", "") or ""),
        "document_number": mask_document_number(getattr(row, "document_number", None)),
        "issue_date": getattr(row, "issue_date", None),
        "expiry_date": getattr(row, "expiry_date", None),
        "media": getattr(row, "media", None) or None,
        "media_id": getattr(row, "media", None) or None,
    }


def serialize_request(doc, *, include_rejection_reason: bool = True) -> dict[str, Any]:
    payload = {
        "name": doc.name,
        "id": doc.name,
        "verification_id": doc.name,
        "account_id": public_account_id_for_user(doc.user),
        "verification_type": doc.verification_type,
        "status": doc.status,
        "verified_on": doc.verified_on,
        "documents": [serialize_document(row) for row in (doc.verification_documents or [])],
    }
    if include_rejection_reason and doc.status == "Rejected":
        payload["rejection_reason"] = str(doc.rejection_reason or "") or None
    return payload
