"""Consistent API response helpers.

All whitelisted endpoints should return a consistent JSON shape:

  ok():   { ok: true,  message: str, data: any }
  fail(): { ok: false, message: str, error: str, data: any }

``error`` is the only public machine-readable failure key. Clients must branch
on ``error``.

The helpers also set frappe.local.response["http_status_code"] so clients and
edge infrastructure can rely on HTTP semantics in addition to the JSON body.
"""

from __future__ import annotations

from typing import Any

import frappe

# Keep this map broad and explicit. Public APIs should return stable HTTP
# semantics so mobile clients, gateways, logs, and monitors can distinguish
# auth, permission, validation, conflict, rate-limit, dependency, and server
# failures without parsing human-readable messages.
DEFAULT_HTTP_STATUS_MAP: dict[str, int] = {
	# Generic request/validation failures.
	"BAD_REQUEST": 400,
	"EMAIL_MISSING": 422,
	"FILE_MISSING": 404,
	"INVALID_AD": 422,
	"INVALID_IDENTIFIER": 422,
	"OTP_INVALID": 400,
	"PASSWORD_MISMATCH": 422,
	"UNSUPPORTED_FILE_TYPE": 415,
	"UNSUPPORTED_MEDIA_TYPE": 415,
	"VALIDATION_ERROR": 422,
	"INVALID_PROFILE_FIELD": 422,
	"INVALID_DISPLAY_NAME": 422,
	"INVALID_LEGAL_NAME": 422,
	"INVALID_PHONE_NUMBER": 422,
	"INVALID_DATE_OF_BIRTH": 422,
	"INVALID_GENDER": 422,
	"INVALID_AVATAR_MEDIA": 422,
	"INVALID_MEDIA_PURPOSE": 422,
	"INVALID_FILE": 422,
	"INVALID_FILENAME": 422,
	"INVALID_CHECKSUM": 422,
	"FILE_TOO_LARGE": 413,
	"SIZE_MISMATCH": 422,
	"CHECKSUM_MISMATCH": 422,
	"INVALID_COUNTRY": 422,
	"INVALID_CURRENCY": 422,
	"DISABLED_CURRENCY": 422,
	"INVALID_LANGUAGE": 422,
	"DISABLED_LANGUAGE": 422,
	"INVALID_LOCATION": 422,
	"INVALID_LIMIT": 422,
	"INVALID_OFFSET": 422,
	"INVALID_SEARCH_QUERY": 422,
	"INVALID_CATALOG_INPUT": 422,
	"INVALID_CATEGORY": 422,
	"INVALID_CATEGORY_SCHEMA": 422,
	"INVALID_CATEGORY_TREE": 422,
	"CATEGORY_NOT_SELLABLE": 422,
	# Authentication/session/token failures.
	"AUTH_REQUIRED": 401,
	"INVALID_CREDENTIALS": 401,
	"LOGIN_FAILED": 401,
	"LOGIN_REQUIRED": 401,
	"SESSION_INVALID": 401,
	"TOKEN_EXPIRED": 410,
	"TOKEN_INVALID": 401,
	"TOKEN_VERIFY_FAILED": 401,
	"UNAUTHENTICATED": 401,
	"UNAUTHORIZED": 401,
	# Permission/account/precondition failures.
	"ACCOUNT_DELETED": 403,
	"ACCOUNT_DEACTIVATED": 403,
	"ACCOUNT_DELETED_RESTORABLE": 403,
	"ACCOUNT_DISABLED": 403,
	"ACCOUNT_SUSPENDED": 403,
	"COMMENTS_DISABLED": 403,
	"EMAIL_NOT_VERIFIED": 403,
	"FORBIDDEN": 403,
	"MEDIA_ACCESS_DENIED": 403,
	"MEDIA_OWNERSHIP_REQUIRED": 403,
	"NOT_VERIFIED": 403,
	"PERMISSION_DENIED": 403,
	"PREFERENCE_MISSING": 403,
	"PROFILE_UNAVAILABLE": 403,
	"SELLER_INACTIVE": 403,
	"SELLER_REQUIRED": 403,
	"USER_BLOCKED": 403,
	# Missing resources.
	"NOT_FOUND": 404,
	"OTP_NOT_FOUND": 404,
	"OTP_RECORD_MISSING": 404,
	"PROFILE_NOT_FOUND": 404,
	"ACCOUNT_NOT_FOUND": 404,
	"MEDIA_NOT_FOUND": 404,
	"RESOURCE_NOT_FOUND": 404,
	"CATEGORY_NOT_FOUND": 404,
	# Expired/deleted resources.
	"EXPIRED": 410,
	"UPLOAD_EXPIRED": 410,
	"EXPIRED_AD": 410,
	"OTP_EXPIRED": 410,
	"RESTORE_EXPIRED": 410,
	# Conflict/idempotency/state failures.
	"ACCOUNT_NOT_DELETED": 409,
	"ACTIVE_CALL_EXISTS": 409,
	"ALREADY_EXISTS": 409,
	"ALREADY_REVIEWED": 409,
	"COHOST_SLOT_UNAVAILABLE": 409,
	"CONFLICT": 409,
	"COMPANION_ACTIVE_GENERATION_AHEAD": 409,
	"COMPANION_CALLBACK_COMPLETE_EVIDENCE_INSUFFICIENT": 409,
	"COMPANION_CALLBACK_COMPLETE_FRAPPE_STATE_MISMATCH": 409,
	"DUPLICATE": 409,
	"INVALID_STATE": 409,
	"MEDIA_NOT_READY": 409,
	"MEDIA_ALREADY_ATTACHED": 409,
	"MEDIA_LIMIT_EXCEEDED": 409,
	"UPLOAD_INCOMPLETE": 409,
	"MARKET_LOCKED": 409,
	"COUNTRY_LOCKED": 409,
	"OTP_USED": 409,
	# Rate limiting / throttling.
	"COOLDOWN": 429,
	"OTP_MAX_ATTEMPTS": 429,
	"RATE_LIMIT": 429,
	"RATE_LIMITED": 429,
	# Upstream/dependency/service availability failures.
	"BACKGROUND_REMOVAL_UNAVAILABLE": 503,
	"CALLBACK_AUTH_NOT_CONFIGURED": 503,
	"CONFIG_ERROR": 503,
	"IMAGE_SEARCH_UNAVAILABLE": 503,
	"MAP_SERVICE_ERROR": 503,
	"SERVICE_UNAVAILABLE": 503,
	"STORAGE_UNAVAILABLE": 503,
	"TRANSLATION_UNAVAILABLE": 503,
	# Upstream processing failures where AOS received/handled a request but a
	# downstream service, worker, or integration failed to complete correctly.
	"ANALYTICS_CALLBACK_FAILED": 502,
	"BACKGROUND_REMOVAL_FAILED": 502,
	"CALLBACK_FAILED": 502,
	"IMAGE_SEARCH_FAILED": 502,
	"MODERATION_CALLBACK_FAILED": 502,
	"SEARCH_RANKING_CALLBACK_FAILED": 502,
	"VIDEO_CALLBACK_FAILED": 502,
	"PROCESSING_FAILED": 502,
	# Internal data or server failures.
	"DATA_ERROR": 500,
	"CATALOG_DATA_ERROR": 500,
	"DELETE_ACCOUNT_FAILED": 500,
	"INTERNAL_ERROR": 500,
	"LOGOUT_FAILED": 500,
	"PREFERENCE_CREATE_FAILED": 500,
	"REGISTER_FAILED": 500,
	"RESTORE_ACCOUNT_FAILED": 500,
	"USER_CREATE_FAILED": 500,
}


def _set_http_status(status: int) -> None:
	# Be defensive: frappe.local may not exist in some edge environments/tests.
	try:
		frappe.local.response["http_status_code"] = int(status)
	except Exception:
		pass


def normalize_error_code(code: str | None) -> str:
	"""Normalize public AOS error codes to a safe, stable token."""

	value = str(code or "INTERNAL_ERROR").strip().upper()
	value = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)
	return value or "INTERNAL_ERROR"


def http_status_for_code(code: str, default: int = 400) -> int:
	"""Return the HTTP status for an AOS error code.

	Unknown codes intentionally default to 400 so older clients still receive a
	deterministic client-error response instead of a misleading 500.
	"""

	return int(DEFAULT_HTTP_STATUS_MAP.get(normalize_error_code(code), default))


def ok(message: str, data: Any = None):
	_set_http_status(200)
	return {"ok": True, "message": str(message or "OK."), "data": {} if data is None else data}


def fail(
	message: str,
	*,
	error: str = "INTERNAL_ERROR",
	data: Any = None,
	http_status: int | None = None,
):
	public_error = normalize_error_code(error)
	status = int(http_status or http_status_for_code(public_error))
	_set_http_status(status)
	return {
		"ok": False,
		"message": str(message or "Request failed."),
		"error": public_error,
		"data": {} if data is None else data,
	}
