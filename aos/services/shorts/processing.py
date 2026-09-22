"""Shorts video-processing orchestration facade.

The companion service is intentionally not a client API.  This module gives the
Shorts domain one vocabulary while the durable implementation lives in
``aos.services.video_processing_service``.
"""
from __future__ import annotations

from typing import Any

from .constants import VIDEO_OPERATION_PROCESS, VIDEO_OPERATIONS
from .errors import ShortsConflictError

_ALLOWED_TRANSITIONS = {
    "Not Required": set(),
    "Queued": {"Processing", "Retry Waiting", "Failed", "Cancelled"},
    "Processing": {"Ready", "Retry Waiting", "Failed", "Cancelled"},
    "Retry Waiting": {"Queued", "Processing", "Failed", "Cancelled"},
    "Ready": {"Queued", "Cancelled"},
    "Failed": {"Queued", "Retry Waiting", "Cancelled"},
    "Cancelled": set(),
}


def require_transition(current: str, target: str) -> None:
    current = str(current or "")
    target = str(target or "")
    if target == current:
        return
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ShortsConflictError(
            "Short processing state changed.",
            code="SHORTS_INVALID_PROCESSING_TRANSITION",
            data={"from": current, "to": target},
        )


def enqueue_processing_job(
    *,
    short: Any,
    operation: str = VIDEO_OPERATION_PROCESS,
    idempotency_key: str | None = None,
    force_new_generation: bool = False,
):
    """Create a durable processing job in the caller transaction.

    ``idempotency_key`` is accepted from the public service so duplicate HTTP
    retries can converge, but the canonical downstream key is derived from the
    Short/operation/generation on the server.
    """
    from aos.services.video_processing_service import create_video_processing_job

    normalized = str(operation or "").strip()
    if normalized not in VIDEO_OPERATIONS:
        raise ShortsConflictError(
            "Invalid video processing operation.",
            code="SHORTS_INVALID_PROCESSING_OPERATION",
        )
    return create_video_processing_job(
        short.name,
        operation=normalized,
        force=bool(force_new_generation),
        reason="shorts_api",
        client_idempotency_key=str(idempotency_key or "").strip() or None,
        enqueue=True,
    )
