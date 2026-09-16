"""Canonical AOS Notification category and payload contracts.

Business domains own the events that cause notifications. This module only
owns the infrastructure-level mapping needed to persist and safely serialize
those events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class NotificationContractError(ValueError):
    """Raised when internal notification material does not match a canonical contract."""


CATEGORY_ALL = "all"
CATEGORY_COMMUNICATION = "communication"
CATEGORY_ACTIVITY = "activity"
CATEGORY_MARKETPLACE = "marketplace"
CATEGORY_ACCOUNT = "account"


@dataclass(frozen=True)
class NotificationTypeContract:
    category: str
    event: str
    allowed_payload_fields: frozenset[str]
    required_payload_fields: frozenset[str]
    actor_scoped: bool = False


_CONTRACTS = {
    "message": NotificationTypeContract(
        category=CATEGORY_COMMUNICATION,
        event="aos_new_message",
        allowed_payload_fields=frozenset(
            {"conversation_id", "sender", "sender_account_id", "message_id"}
        ),
        required_payload_fields=frozenset({"conversation_id", "sender_account_id"}),
        actor_scoped=True,
    ),
    "missed_call": NotificationTypeContract(
        category=CATEGORY_COMMUNICATION,
        event="aos_missed_call",
        allowed_payload_fields=frozenset(
            {"call_id", "caller", "caller_account_id", "type", "notification_type"}
        ),
        required_payload_fields=frozenset({"call_id", "caller_account_id"}),
        actor_scoped=True,
    ),
    "follow": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_follow",
        allowed_payload_fields=frozenset({"follower", "account_id"}),
        required_payload_fields=frozenset({"follower"}),
        actor_scoped=True,
    ),
    "new_short": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_new_short",
        allowed_payload_fields=frozenset({"short_id", "actor"}),
        required_payload_fields=frozenset({"short_id", "actor"}),
        actor_scoped=True,
    ),
    "short_like": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_short_like",
        allowed_payload_fields=frozenset({"short_id", "actor"}),
        required_payload_fields=frozenset({"short_id", "actor"}),
        actor_scoped=True,
    ),
    "short_comment": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_short_comment",
        allowed_payload_fields=frozenset({"short_id", "actor", "content"}),
        required_payload_fields=frozenset({"short_id", "actor"}),
        actor_scoped=True,
    ),
    "short_mention": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_short_mention",
        allowed_payload_fields=frozenset({"short_id", "actor", "source_type", "comment_id"}),
        required_payload_fields=frozenset({"short_id", "actor"}),
        actor_scoped=True,
    ),
    "comment_reply": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_comment_reply",
        allowed_payload_fields=frozenset({"comment_id", "short_id", "actor", "content"}),
        required_payload_fields=frozenset({"comment_id", "actor"}),
        actor_scoped=True,
    ),
    "live_started": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_live_started",
        allowed_payload_fields=frozenset({"live_id", "host_user"}),
        required_payload_fields=frozenset({"live_id", "host_user"}),
        actor_scoped=True,
    ),
    "media_processing_failed": NotificationTypeContract(
        category=CATEGORY_ACTIVITY,
        event="aos_media_processing_failed",
        allowed_payload_fields=frozenset({"processing_job_id", "source_media_id", "operation"}),
        required_payload_fields=frozenset({"processing_job_id", "source_media_id", "operation"}),
    ),
    "seller_status_changed": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_seller_status_changed",
        allowed_payload_fields=frozenset({"seller_id", "status", "reason_code"}),
        required_payload_fields=frozenset({"seller_id", "status"}),
    ),
    "ad_approved": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_ad_approved",
        allowed_payload_fields=frozenset({"ad_id"}),
        required_payload_fields=frozenset({"ad_id"}),
    ),
    "ad_rejected": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_ad_rejected",
        allowed_payload_fields=frozenset({"ad_id"}),
        required_payload_fields=frozenset({"ad_id"}),
    ),
    "ad_expired": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_ad_expired",
        allowed_payload_fields=frozenset({"ad_id"}),
        required_payload_fields=frozenset({"ad_id"}),
    ),
    "review_received": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_review_received",
        allowed_payload_fields=frozenset({"review_id", "ad_id"}),
        required_payload_fields=frozenset({"review_id", "ad_id"}),
        actor_scoped=True,
    ),
    "review_approved": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_review_approved",
        allowed_payload_fields=frozenset({"review_id", "ad_id"}),
        required_payload_fields=frozenset({"review_id", "ad_id"}),
    ),
    "review_rejected": NotificationTypeContract(
        category=CATEGORY_MARKETPLACE,
        event="aos_review_rejected",
        allowed_payload_fields=frozenset({"review_id", "ad_id"}),
        required_payload_fields=frozenset({"review_id", "ad_id"}),
    ),
    "verification_approved": NotificationTypeContract(
        category=CATEGORY_ACCOUNT,
        event="aos_verification_approved",
        allowed_payload_fields=frozenset({"account_id", "verification_id"}),
        required_payload_fields=frozenset({"account_id"}),
    ),
    "verification_rejected": NotificationTypeContract(
        category=CATEGORY_ACCOUNT,
        event="aos_verification_rejected",
        allowed_payload_fields=frozenset({"account_id", "verification_id"}),
        required_payload_fields=frozenset({"account_id"}),
    ),
}

NOTIFICATION_TYPE_CONTRACTS: Mapping[str, NotificationTypeContract] = MappingProxyType(_CONTRACTS)
NOTIFICATION_TYPES = frozenset(_CONTRACTS)
CATEGORY_TYPES = MappingProxyType(
    {
        CATEGORY_COMMUNICATION: tuple(
            kind for kind, contract in _CONTRACTS.items() if contract.category == CATEGORY_COMMUNICATION
        ),
        CATEGORY_ACTIVITY: tuple(
            kind for kind, contract in _CONTRACTS.items() if contract.category == CATEGORY_ACTIVITY
        ),
        CATEGORY_MARKETPLACE: tuple(
            kind for kind, contract in _CONTRACTS.items() if contract.category == CATEGORY_MARKETPLACE
        ),
        CATEGORY_ACCOUNT: tuple(
            kind for kind, contract in _CONTRACTS.items() if contract.category == CATEGORY_ACCOUNT
        ),
    }
)
VALID_CATEGORIES = (
    CATEGORY_ALL,
    CATEGORY_COMMUNICATION,
    CATEGORY_ACTIVITY,
    CATEGORY_MARKETPLACE,
    CATEGORY_ACCOUNT,
)

MAX_NOTIFICATION_TITLE_LENGTH = 140
MAX_NOTIFICATION_BODY_LENGTH = 500
MAX_NOTIFICATION_DEDUPE_KEY_LENGTH = 180
MAX_NOTIFICATION_PAYLOAD_BYTES = 8 * 1024
MAX_PUBLIC_PAYLOAD_TEXT_LENGTH = 2048


def contract_for(notification_type: str) -> NotificationTypeContract:
    key = str(notification_type or "").strip()
    try:
        return NOTIFICATION_TYPE_CONTRACTS[key]
    except KeyError as exc:
        raise NotificationContractError("Unsupported notification type.") from exc


def canonical_event(notification_type: str) -> str:
    return contract_for(notification_type).event


def _normalize_payload_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.replace("\x00", "").strip()
        return text[:MAX_PUBLIC_PAYLOAD_TEXT_LENGTH]
    # Canonical persistent notification builders currently use scalar values.
    # Do not allow arbitrary nested dictionaries/lists to become public push or
    # inbox payloads by accident.
    raise NotificationContractError("Notification payload values must be scalar.")


def validate_persistent_payload(notification_type: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    contract = contract_for(notification_type)
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise NotificationContractError("Notification payload must be an object.")

    unknown = sorted(set(payload) - set(contract.allowed_payload_fields))
    if unknown:
        raise NotificationContractError("Unsupported notification payload field.")

    clean = {
        str(key): _normalize_payload_value(value)
        for key, value in payload.items()
        if key in contract.allowed_payload_fields and value is not None
    }
    missing = [
        field
        for field in contract.required_payload_fields
        if clean.get(field) in (None, "")
    ]
    if missing:
        raise NotificationContractError("Notification payload is incomplete.")

    encoded = json.dumps(clean, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > MAX_NOTIFICATION_PAYLOAD_BYTES:
        raise NotificationContractError("Notification payload is too large.")
    return clean


def sanitize_public_payload(notification_type: str, payload: Any) -> dict[str, Any]:
    """Return only category-contracted public fields from a stored payload.

    Historical rows can predate the current builders, so serialization is
    deliberately fail-closed rather than raising and breaking the inbox.
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload) if payload.strip() else {}
        return validate_persistent_payload(notification_type, payload if isinstance(payload, Mapping) else {})
    except Exception:
        contract = NOTIFICATION_TYPE_CONTRACTS.get(str(notification_type or "").strip())
        if not contract or not isinstance(payload, Mapping):
            return {}
        safe: dict[str, Any] = {}
        for key in contract.allowed_payload_fields:
            if key not in payload:
                continue
            try:
                safe[key] = _normalize_payload_value(payload[key])
            except NotificationContractError:
                continue
        return safe
