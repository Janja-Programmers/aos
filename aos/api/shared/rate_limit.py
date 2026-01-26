"""Lightweight rate limiting utilities.

We intentionally keep rate limiting logic small and dependency-free. The cache backend
is Redis in most Frappe deployments, so we prefer atomic incr when available.
"""

from __future__ import annotations

import frappe

from .responses import fail


def request_ip() -> str:
    """Best-effort client IP."""
    try:
        return frappe.local.request_ip or "unknown"
    except Exception:
        return "unknown"


def cache_incr(key: str, ttl_seconds: int) -> int:
    """Increment a cached counter with TTL.

    Tries to use atomic `cache.incr` when available. Ensures a TTL is set.
    """

    cache = frappe.cache()

    # Prefer atomic increments (Redis).
    try:
        val = cache.incr(key)
        # ensure TTL exists (best effort)
        try:
            cache.expire(key, ttl_seconds)
        except Exception:
            # Some cache implementations may not expose expire.
            pass
        return int(val)
    except Exception:
        # Fallback for older/mocked cache.
        val = int(cache.get_value(key) or 0) + 1
        cache.set_value(key, val, expires_in_sec=ttl_seconds)
        return int(val)


def rate_limit(key: str, ttl_seconds: int, limit: int, message: str):
    """Return fail(...) when rate limit exceeded, otherwise None."""
    if cache_incr(key, ttl_seconds) > int(limit):
        return fail(message, code="RATE_LIMIT")
    return None
