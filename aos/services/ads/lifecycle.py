"""Explicit Ads lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import (
    ACTION_DELETE,
    ACTION_MARK_AVAILABLE,
    ACTION_MARK_SOLD,
    ACTION_RENEW,
    AD_STATUSES,
    STATUS_ACTIVE,
    STATUS_DECLINED,
    STATUS_DELETED,
    STATUS_EXPIRED,
    STATUS_REVIEWING,
    STATUS_SOLD,
    STATUS_SUSPENDED,
)
from .errors import AdsConflictError, AdsValidationError


@dataclass(frozen=True)
class Transition:
    old_status: str
    new_status: str
    action: str
    changed: bool = True


_SELLER_TRANSITIONS: dict[str, tuple[frozenset[str], str, frozenset[str]]] = {
    ACTION_MARK_SOLD: (frozenset({STATUS_ACTIVE}), STATUS_SOLD, frozenset({STATUS_SOLD})),
    ACTION_MARK_AVAILABLE: (frozenset({STATUS_SOLD}), STATUS_ACTIVE, frozenset({STATUS_ACTIVE})),
    ACTION_RENEW: (frozenset({STATUS_EXPIRED}), STATUS_ACTIVE, frozenset()),
    ACTION_DELETE: (
        frozenset({STATUS_REVIEWING, STATUS_DECLINED, STATUS_SOLD, STATUS_EXPIRED}),
        STATUS_DELETED,
        frozenset({STATUS_DELETED}),
    ),
}

_SYSTEM_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "moderation_allow": (frozenset({STATUS_REVIEWING, STATUS_DECLINED, STATUS_ACTIVE}), STATUS_ACTIVE),
    "moderation_reject": (frozenset({STATUS_REVIEWING, STATUS_ACTIVE}), STATUS_DECLINED),
    "moderation_review": (frozenset({STATUS_REVIEWING, STATUS_DECLINED, STATUS_ACTIVE}), STATUS_REVIEWING),
    "seller_resubmit": (frozenset({STATUS_REVIEWING, STATUS_DECLINED}), STATUS_REVIEWING),
    "expire": (frozenset({STATUS_ACTIVE}), STATUS_EXPIRED),
    "suspend": (
        frozenset({STATUS_REVIEWING, STATUS_ACTIVE, STATUS_DECLINED, STATUS_SOLD, STATUS_EXPIRED}),
        STATUS_SUSPENDED,
    ),
}


def normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        raise AdsValidationError("Invalid ad status.", code="INVALID_AD_INPUT")
    status = value.strip()
    if status not in AD_STATUSES:
        raise AdsValidationError("Invalid ad status.", code="INVALID_AD_INPUT")
    return status


def transition_for_action(action: Any, current_status: Any) -> Transition:
    if not isinstance(action, str):
        raise AdsValidationError("Invalid action.", code="INVALID_AD_INPUT")
    clean_action = action.strip().lower()
    status = normalize_status(current_status)
    rule = _SELLER_TRANSITIONS.get(clean_action)
    if not rule:
        raise AdsValidationError("Invalid action.", code="INVALID_AD_INPUT")
    allowed, target, idempotent_states = rule
    if status in idempotent_states:
        return Transition(status, status, clean_action, changed=False)
    if status not in allowed:
        raise AdsConflictError("The ad is not in a valid state for this action.")
    return Transition(status, target, clean_action, changed=status != target)


def validate_status_transition(old_status: Any, new_status: Any, *, action: Any) -> Transition:
    old = normalize_status(old_status)
    new = normalize_status(new_status)
    if not isinstance(action, str) or not action.strip():
        raise AdsConflictError("Ad status changes require an explicit lifecycle action.")
    clean_action = action.strip().lower()
    if clean_action in _SELLER_TRANSITIONS:
        transition = transition_for_action(clean_action, old)
        if transition.new_status != new:
            raise AdsConflictError("Invalid ad status transition.")
        return transition
    rule = _SYSTEM_TRANSITIONS.get(clean_action)
    if not rule:
        raise AdsConflictError("Invalid ad lifecycle action.")
    allowed, target = rule
    if old not in allowed or new != target:
        raise AdsConflictError("Invalid ad status transition.")
    return Transition(old, new, clean_action, changed=old != new)


def is_public_status(status: Any) -> bool:
    return str(status or "").strip() == STATUS_ACTIVE
