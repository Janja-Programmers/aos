"""Secret-safe diagnostic helpers for AOS and Frappe error logging.

This module deliberately has no module-level Frappe import so it can be used by
repository/unit tests and can be installed early in request/job lifecycles.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "********"
_SANITIZER_FAILURE = "[error details redacted: secure logging sanitizer failed]"
_SENTRY_REDACTION_INSTALLED = False

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "secret_key",
        "api_secret",
        "api_key",
        "access_key",
        "private_key",
        "client_secret",
        "service_secret",
        "callback_secret",
        "request_secret",
        "internal_secret",
        "classification_secret",
        "sid",
        "session_id",
        "token",
        "x_aos_signature",
        "livekit_keys",
    }
)

# Match repr/JSON/query-style key-value pairs. The suffix forms intentionally
# cover names such as dispatch_token and callback_secret without treating
# benign names such as object_key or max_tokens_per_multicast as secrets.
_SENSITIVE_KEY_TEXT = (
    r"(?:authorization|proxy[-_]authorization|cookie|set[-_]cookie|"
    r"password|passwd|pwd|secret|secret_key|api_secret|api_key|access_key|"
    r"private_key|client_secret|service_secret|callback_secret|request_secret|"
    r"internal_secret|classification_secret|sid|session_id|token|x[-_]aos[-_]signature|"
    r"livekit_keys|[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*_(?:secret|token|password|passwd|pwd))"
)
_KEY_VALUE_RE = re.compile(
    rf"(?P<prefix>(?P<keyquote>['\"]?){_SENSITIVE_KEY_TEXT}(?P=keyquote)\s*[:=]\s*)"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s,;&\)\]\}]+)",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"(?P<prefix>\b(?:authorization|proxy[-_]authorization)\s*[:=]\s*(?:Bearer|Basic)\s+)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+(?P<value>[A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)
_URI_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>\b(?:mysql|mariadb|postgres(?:ql)?|redis(?:s)?|https?)://"
    r"[^:/@\s]+:)(?P<password>[^@/\s]+)(?P<suffix>@)",
    re.IGNORECASE,
)


def is_sensitive_key(key: object) -> bool:
    """Return whether a structured field name should be treated as secret."""
    normalized = str(key or "").strip().lower().replace("-", "_")
    if not normalized:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return normalized.endswith(("_secret", "_token", "_password", "_passwd", "_pwd"))


def _redacted_value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        return f"{raw[0]}{REDACTED}{raw[-1]}"
    return REDACTED


def redact_sensitive_text(value: object) -> str:
    """Redact common credential forms from already-rendered diagnostics."""
    text = str(value or "")
    if not text:
        return text

    text = _AUTH_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _URI_CREDENTIAL_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}{match.group('suffix')}",
        text,
    )
    return _KEY_VALUE_RE.sub(
        lambda match: f"{match.group('prefix')}{_redacted_value(match.group('value'))}",
        text,
    )


def redact_sensitive_data(value: Any) -> Any:
    """Return a redacted copy of structured diagnostic data."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, set):
        return {redact_sensitive_data(item) for item in value}
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_error_log_document(doc: Any, _method: str | None = None) -> None:
    """Fail closed when an Error Log is about to be persisted.

    Frappe contextual tracebacks may render whole local objects. This second
    boundary protects explicit ``frappe.log_error(message=...)`` calls too.
    """
    try:
        for fieldname in ("error", "metadata", "method"):
            current = getattr(doc, fieldname, None)
            if current is not None:
                setattr(doc, fieldname, redact_sensitive_text(current))
    except Exception:
        # Error logging must not create a recursive error, and a sanitizer
        # failure must never fall back to storing the unsanitized diagnostic.
        if hasattr(doc, "error"):
            doc.error = _SANITIZER_FAILURE
        if hasattr(doc, "metadata"):
            doc.metadata = "{}"
        if hasattr(doc, "method"):
            doc.method = "Secure logging sanitizer failure"


def sanitize_sentry_event(event: Any, _hint: Any = None) -> Any:
    """Redact a Sentry event, including request context and frame-local reprs."""
    try:
        return redact_sensitive_data(event)
    except Exception:
        # Telemetry is optional; fail closed rather than transmitting an event
        # that could contain credentials when sanitization itself fails.
        return None


def install_sentry_redaction() -> bool:
    """Install an idempotent global Sentry event processor when available."""
    global _SENTRY_REDACTION_INSTALLED
    if _SENTRY_REDACTION_INSTALLED:
        return True

    try:
        import sentry_sdk
    except Exception:
        return False

    try:
        sentry_sdk.get_global_scope().add_event_processor(sanitize_sentry_event)
        _SENTRY_REDACTION_INSTALLED = True
        return True
    except Exception:
        return False


def install_frappe_traceback_redaction(*_args: Any, **_kwargs: Any) -> bool:
    """Wrap Frappe traceback rendering so secrets are removed before telemetry.

    The installation is idempotent and patches both exported references used by
    Frappe. It is invoked from both request and background-job hooks.
    """
    try:
        import frappe
        import frappe.utils as frappe_utils
    except Exception:
        return False

    original = getattr(frappe, "get_traceback", None)
    if original is None:
        return False
    if getattr(original, "_aos_secret_safe", False):
        install_sentry_redaction()
        return True

    @functools.wraps(original)
    def secure_get_traceback(*args: Any, **kwargs: Any) -> str:
        try:
            return redact_sensitive_text(original(*args, **kwargs))
        except Exception:
            return _SANITIZER_FAILURE

    secure_get_traceback._aos_secret_safe = True  # type: ignore[attr-defined]
    secure_get_traceback._aos_original = original  # type: ignore[attr-defined]
    frappe.get_traceback = secure_get_traceback
    frappe_utils.get_traceback = secure_get_traceback
    install_sentry_redaction()
    return True
