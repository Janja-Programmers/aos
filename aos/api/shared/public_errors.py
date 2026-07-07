"""Helpers for safe public API error messages.

Public endpoints should not return raw exception strings because framework,
SQL, filesystem, upstream-service, and secret values can appear in exception
messages. These helpers keep client-facing errors stable while still allowing
short, known-safe validation messages where they are useful.
"""

from __future__ import annotations

import re
from typing import Any

import frappe

from aos.api.shared.responses import fail

_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"traceback",
        r"\b(file|line)\s+\d+\b",
        r"\b(programmingerror|operationalerror|integrityerror|databaseerror|internalerror)\b",
        r"\b(pymysql|mariadb|mysql|sqlalchemy|frappe\.|werkzeug\.|requests\.|urllib3\.)\b",
        r"\b(select|insert|update|delete|drop|alter|create)\s+.+\b(from|into|table|where|set)\b",
        r"`tab[^`]+`",
        r"/home/|/var/|/mnt/|/tmp/|\\\\|[a-z]:\\\\",
        r"\b(password|passwd|secret|token|api[_-]?key|authorization|credential|signature)\b",
        r"aws4-hmac|x-amz-|access[_-]?key|minio|livekit|firebase",
        r"https?://[^\s]+",
        r"\b[0-9a-f]{32,}\b",
    )
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_PUBLIC_MESSAGE_LENGTH = 180


def is_sensitive_exception_message(message: Any) -> bool:
    """Return True when a message looks unsafe for client responses."""

    text = str(message or "").strip()
    if not text:
        return False
    if _CONTROL_CHARS.search(text):
        return True
    if len(text) > _MAX_PUBLIC_MESSAGE_LENGTH:
        return True
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


def safe_exception_message(exc: Exception, fallback: str) -> str:
    """Return a client-safe message for an exception.

    Short domain validation messages such as "Ad not found" or "Invalid OTP" are
    kept. Messages that look like SQL/framework/upstream/secret details are
    replaced by ``fallback``.
    """

    text = str(exc or "").strip()
    fallback_text = str(fallback or "Something went wrong.").strip() or "Something went wrong."
    if not text:
        return fallback_text
    if is_sensitive_exception_message(text):
        return fallback_text
    return text


def log_exception(title: str) -> None:
    """Log the current exception traceback without affecting response flow."""

    try:
        frappe.log_error(frappe.get_traceback(), title)
    except Exception:
        pass


def safe_fail_from_exception(
    exc: Exception,
    *,
    fallback: str,
    code: str = "VALIDATION_ERROR",
    http_status: int | None = None,
    log_title: str | None = None,
):
    """Return fail() with a sanitized exception message.

    If a log title is provided, the full traceback is preserved server-side.
    """

    if log_title:
        log_exception(log_title)
    return fail(
        safe_exception_message(exc, fallback),
        code=code,
        http_status=http_status,
    )
