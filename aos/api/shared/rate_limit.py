"""Lightweight rate limiting utilities.

Rate-limit keys must never include passwords, tokens, raw cookies, or long
untrusted values. Use ``rate_limit_key`` for auth/session endpoints so key parts
are bounded and hashed before they reach Redis/logging/debug tooling.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import frappe

from .responses import fail

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9:_-]+")


def request_ip() -> str:
    """Best-effort client IP, with bounded/sanitized fallback."""

    try:
        ip = frappe.local.request_ip or "unknown"
    except Exception:
        ip = "unknown"
    return _SAFE_KEY_RE.sub("_", str(ip).strip())[:128] or "unknown"


def safe_rate_limit_part(value: Any, *, max_clear_length: int = 64) -> str:
    """Return a bounded, non-sensitive cache-key component.

    Values that are short and already safe remain readable. Long or unsafe input
    is SHA-256 hashed so attacker-controlled identifiers cannot poison cache keys
    or leak tokens/headers through infrastructure tooling.
    """

    raw = str(value or "").strip().lower()
    if not raw:
        return "blank"

    safe = _SAFE_KEY_RE.sub("_", raw)
    if safe == raw and len(safe) <= max_clear_length:
        return safe

    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def rate_limit_key(*parts: Any) -> str:
    """Build an AOS rate-limit key from bounded safe components."""

    return "aos:rl:" + ":".join(safe_rate_limit_part(part) for part in parts)


def cache_incr(key: str, ttl_seconds: int) -> int:
    """Increment a cached counter with TTL.

    Tries to use atomic `cache.incr` when available. Ensures a TTL is set.
    """

    cache = frappe.cache()
    safe_key = str(key or "").strip()[:512]

    # Prefer atomic increments (Redis).
    try:
        val = cache.incr(safe_key)
        # ensure TTL exists (best effort)
        try:
            cache.expire(safe_key, ttl_seconds)
        except Exception:
            # Some cache implementations may not expose expire.
            pass
        return int(val)
    except Exception:
        # Fallback for older/mocked cache.
        val = int(cache.get_value(safe_key) or 0) + 1
        cache.set_value(safe_key, val, expires_in_sec=ttl_seconds)
        return int(val)


def rate_limit(key: str, ttl_seconds: int, limit: int, message: str):
    """Return fail(...) when rate limit exceeded, otherwise None."""

    if cache_incr(key, ttl_seconds) > int(limit):
        return fail(message, error="RATE_LIMIT")
    return None
