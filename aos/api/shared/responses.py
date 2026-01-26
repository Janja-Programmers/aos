"""Consistent API response helpers.

All whitelisted endpoints should return a consistent JSON shape:

  ok():   { ok: true,  message: str, data: any }
  fail(): { ok: false, message: str, code: str, data: any }

Also sets frappe.local.response["http_status_code"] so mobile clients can rely on
HTTP semantics in addition to the JSON body.
"""

from __future__ import annotations

from typing import Any

import frappe


DEFAULT_HTTP_STATUS_MAP: dict[str, int] = {
    "VALIDATION_ERROR": 400,
    "NOT_FOUND": 404,
    "OTP_NOT_FOUND": 404,
    "OTP_INVALID": 400,
    "OTP_EXPIRED": 410,
    "OTP_USED": 409,
    "OTP_MAX_ATTEMPTS": 429,
    "COOLDOWN": 429,
    "PASSWORD_MISMATCH": 400,
    "TOKEN_INVALID": 401,
    "TOKEN_EXPIRED": 410,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "RATE_LIMIT": 429,
    "INTERNAL_ERROR": 500,
}


def _set_http_status(status: int) -> None:
    # Be defensive: frappe.local may not exist in some edge environments/tests.
    try:
        frappe.local.response["http_status_code"] = int(status)
    except Exception:
        pass


def ok(message: str, data: Any = None):
    _set_http_status(200)
    return {"ok": True, "message": message, "data": data}


def fail(
    message: str,
    *,
    code: str = "INTERNAL_ERROR",
    data: Any = None,
    http_status: int | None = None,
):
    status = int(http_status or DEFAULT_HTTP_STATUS_MAP.get(code, 400))
    _set_http_status(status)
    return {"ok": False, "message": message, "code": code, "data": data}
