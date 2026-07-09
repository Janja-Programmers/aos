"""Strict security helpers for public service callback endpoints.

Public callbacks are the boundary between AOS and external worker services.
They must be signed, timestamped, JSON-body-only requests. Never fall back to
Frappe kwargs/form/query data for these endpoints because that can bypass HMAC
verification when the raw request body is empty.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import frappe

from aos.utils.aos_config import get_env_int


DEFAULT_CALLBACK_TIMESTAMP_HEADER = "X-AOS-Callback-Timestamp"
DEFAULT_CALLBACK_MAX_AGE_SECONDS = 300


class CallbackSecurityError(Exception):
    """Raised when a public callback request fails security validation."""

    def __init__(self, message: str, error: str = "UNAUTHORIZED", http_status: int = 401):
        super().__init__(message)
        self.message = message
        self.error = error
        self.http_status = int(http_status)


def build_timestamped_signature_payload(raw_body: bytes, timestamp: str) -> bytes:
    """Return the exact bytes signed by callback workers.

    Signing the timestamp together with the raw JSON body prevents an attacker
    from replaying an old valid body/signature with a freshly edited timestamp.
    """

    timestamp_text = str(timestamp or "").strip()
    return timestamp_text.encode("utf-8") + b"." + (raw_body or b"")


def _request_body() -> bytes:
    try:
        request = getattr(frappe, "request", None)
        if request:
            return request.get_data() or b""
    except Exception:
        pass
    return b""


def _header(name: str) -> str | None:
    try:
        value = frappe.get_request_header(name)
    except Exception:
        value = None
    value = str(value or "").strip()
    return value or None


def _max_age_seconds() -> int:
    return get_env_int(
        "AOS_CALLBACK_MAX_AGE_SECONDS",
        DEFAULT_CALLBACK_MAX_AGE_SECONDS,
        min_value=30,
        max_value=3600,
    )


def _validate_timestamp(timestamp: str | None, *, max_age_seconds: int) -> str:
    if not timestamp:
        raise CallbackSecurityError("Missing callback timestamp.")

    timestamp_text = str(timestamp or "").strip()
    try:
        timestamp_seconds = int(float(timestamp_text))
    except Exception as exc:
        raise CallbackSecurityError("Invalid callback timestamp.") from exc

    now_seconds = int(time.time())
    if abs(now_seconds - timestamp_seconds) > int(max_age_seconds):
        raise CallbackSecurityError("Expired callback timestamp.")

    return timestamp_text


def read_signed_json_callback_payload(
    *,
    callback_name: str,
    callback_secret: str,
    signature_header: str,
    verify_signature: Callable[[str, bytes, str | None], bool],
    timestamp_header: str = DEFAULT_CALLBACK_TIMESTAMP_HEADER,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """Read and verify a public callback request as strict signed JSON.

    Rules:
    - callback secret must exist;
    - raw request body must exist;
    - callback timestamp must exist and be fresh;
    - signature must exist and verify over ``timestamp + '.' + raw_body``;
    - body must decode to a JSON object.
    """

    name = str(callback_name or "callback").strip() or "callback"
    secret = str(callback_secret or "").strip()
    if not secret:
        try:
            frappe.log_error(f"Missing callback secret for {name}.", "AOS callback auth misconfigured")
        except Exception:
            pass
        raise CallbackSecurityError(
            "Callback authentication is not configured.",
            error="CALLBACK_AUTH_NOT_CONFIGURED",
            http_status=503,
        )

    raw_body = _request_body()
    if not raw_body or not raw_body.strip():
        raise CallbackSecurityError("Missing callback body.", error="VALIDATION_ERROR", http_status=400)

    signature = _header(signature_header)
    if not signature:
        raise CallbackSecurityError("Missing callback signature.")

    timestamp = _validate_timestamp(
        _header(timestamp_header),
        max_age_seconds=int(max_age_seconds or _max_age_seconds()),
    )

    signed_payload = build_timestamped_signature_payload(raw_body, timestamp)
    if not verify_signature(secret, signed_payload, signature):
        raise CallbackSecurityError("Invalid callback signature.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise CallbackSecurityError("Invalid JSON callback body.", error="VALIDATION_ERROR", http_status=400) from exc

    if not isinstance(payload, dict):
        raise CallbackSecurityError("Callback body must be a JSON object.", error="VALIDATION_ERROR", http_status=400)

    return payload
