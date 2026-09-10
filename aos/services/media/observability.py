"""Bounded media logs and metrics without user, filename, URL, or object-key labels."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager

import frappe

_ALLOWED_EVENTS = {
    "upload_initiated",
    "upload_completed",
    "upload_rejected",
    "upload_failed",
    "attachment_completed",
    "replacement_completed",
    "delete_requested",
    "delete_completed",
    "delete_failed",
    "cleanup_completed",
    "processing_queued",
    "processing_started",
    "processing_completed",
    "processing_failed",
    "processing_enqueue_failed",
    "processing_retry_scheduled",
    "multipart_upload_completed",
    "multipart_upload_aborted",
    "delete_enqueue_failed",
    "storage_operation",
}
_ALLOWED_OUTCOMES = {"success", "rejected", "retryable_failure", "failure"}
_ALLOWED_OPERATIONS = {"init", "confirm", "read", "attach", "replace", "delete", "cleanup", "process", "storage"}


def media_log(
    event: str,
    *,
    media_id: str | None = None,
    purpose: str | None = None,
    operation: str | None = None,
    outcome: str = "success",
    failure_category: str | None = None,
    duration_ms: int | None = None,
    retry_count: int | None = None,
    bytes_count: int | None = None,
) -> None:
    safe_event = event if event in _ALLOWED_EVENTS else "storage_operation"
    safe_operation = operation if operation in _ALLOWED_OPERATIONS else "storage"
    safe_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "failure"
    payload = {
        "event": safe_event,
        "media_id": str(media_id or "")[:64] or None,
        "purpose": str(purpose or "")[:64] or None,
        "operation": safe_operation,
        "outcome": safe_outcome,
        "failure_category": str(failure_category or "")[:48] or None,
        "duration_ms": max(0, int(duration_ms or 0)) if duration_ms is not None else None,
        "retry_count": max(0, int(retry_count or 0)) if retry_count is not None else None,
        "bytes": max(0, int(bytes_count or 0)) if bytes_count is not None else None,
    }
    try:
        frappe.logger("aos.media").info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        pass
    try:
        from aos.utils.metrics import record_media_event

        record_media_event(
            event=safe_event,
            purpose=str(purpose or "unknown"),
            outcome=safe_outcome,
            bytes_count=bytes_count,
            duration_seconds=(duration_ms / 1000) if duration_ms is not None else None,
        )
    except Exception:
        pass


@contextmanager
def media_operation_timer(
    operation: str,
    *,
    media_id: str | None = None,
    purpose: str | None = None,
) -> Iterator[dict[str, object]]:
    started = time.perf_counter()
    context: dict[str, object] = {"outcome": "success", "failure_category": None}
    try:
        yield context
    finally:
        duration_ms = int(max(0.0, time.perf_counter() - started) * 1000)
        media_log(
            "storage_operation",
            media_id=media_id,
            purpose=purpose,
            operation=operation,
            outcome=str(context.get("outcome") or "failure"),
            failure_category=str(context.get("failure_category") or "") or None,
            duration_ms=duration_ms,
        )
