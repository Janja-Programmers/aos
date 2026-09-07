"""Secret-safe Authentication observability helpers."""

from __future__ import annotations

import json

import frappe


def log_auth_exception(title: str, exc: BaseException, *, operation: str) -> None:
    """Record useful failure classification without traceback locals or secrets.

    Authentication request frames may contain passwords, OTPs, reset tokens,
    OIDC tokens, cookies, or session IDs. Do not persist those frames/messages in
    Error Log. The operation plus exception class is safe enough for alerting and
    correlation with infrastructure metrics; deeper diagnosis should use a
    controlled reproduction or redacted framework telemetry.
    """
    payload = {
        "operation": str(operation or "unknown")[:80],
        "exception_type": type(exc).__name__[:120],
    }
    frappe.log_error(message=json.dumps(payload, sort_keys=True), title=str(title or "AOS Auth Failure")[:140])
