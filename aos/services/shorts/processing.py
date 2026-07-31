"""Shorts processing state machine helpers."""

from __future__ import annotations

from .errors import ShortsConflictError

_ALLOWED_TRANSITIONS = {
    "initialized": {"uploaded", "deleted"},
    "uploaded": {"processing", "failed", "deleted"},
    "processing": {"ready", "failed", "deleted"},
    "failed": {"processing", "deleted"},
    "ready": {"processing", "deleted"},
    "deleted": set(),
}


def require_transition(current: str, target: str) -> None:
    current = str(current or "").lower()
    target = str(target or "").lower()
    if target == current:
        return
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ShortsConflictError(
            "Short processing state changed.",
            code="SHORTS_INVALID_PROCESSING_TRANSITION",
            data={"from": current, "to": target},
        )
