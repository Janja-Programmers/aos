import frappe

DEFAULT_HTTP_STATUS_MAP = {
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

def ok(message: str, data=None):
    frappe.local.response["http_status_code"] = 200
    return {
        "ok": True,
        "message": message,
        "data": data,
    }

def fail(message: str, *, code: str = "INTERNAL_ERROR", data=None, http_status: int | None = None):
    status = http_status or DEFAULT_HTTP_STATUS_MAP.get(code, 400)
    frappe.local.response["http_status_code"] = status
    return {
        "ok": False,
        "message": message,
        "code": code,
        "data": data,
    }
