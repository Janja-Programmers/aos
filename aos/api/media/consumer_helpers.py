"""Shared compatibility helpers for feature-level media consumers."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.public_errors import safe_exception_message
from aos.api.shared.responses import fail
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_media_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("media") or value.get("id") or value.get("name")
    return clean_str(value)


def public_media_url(media_id: Any) -> str:
    clean_id = normalize_media_id(media_id)
    if not clean_id:
        return ""
    try:
        return MediaService().get_public_url(clean_id)
    except Exception:
        return ""


def compatibility_media_error(exc: Exception, *, label: str, log_title: str):
    if isinstance(exc, MediaNotFoundError):
        return fail(f"{label} media not found.", error="NOT_FOUND")
    if isinstance(exc, MediaPermissionError):
        return fail(safe_exception_message(exc, "Not allowed."), error="FORBIDDEN")
    if isinstance(exc, MediaValidationError):
        return fail(safe_exception_message(exc, f"Invalid {label.lower()} media."), error="VALIDATION_ERROR")
    frappe.log_error(frappe.get_traceback(), log_title)
    return fail(f"Failed to validate {label.lower()} media.", error="INTERNAL_ERROR")
