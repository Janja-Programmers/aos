import frappe

from .responses import fail


def request_ip() -> str:
    try:
        return frappe.local.request_ip or "unknown"
    except Exception:
        return "unknown"


def cache_incr(key: str, ttl_seconds: int) -> int:
    cache = frappe.cache()
    val = cache.get_value(key) or 0
    val = int(val) + 1
    cache.set_value(key, val, expires_in_sec=ttl_seconds)
    return val


def rate_limit(key: str, ttl_seconds: int, limit: int, message: str):
    if cache_incr(key, ttl_seconds) > limit:
        return fail(message, code="RATE_LIMITED")
    return None
