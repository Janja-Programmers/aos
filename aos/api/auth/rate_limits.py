"""Authentication-specific shared Redis rate limiting.

Security-sensitive auth endpoints fail closed when Redis is unavailable. The Lua
script makes INCR + first-window EXPIRE atomic across all application nodes.
"""

from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit_key, request_ip
from aos.api.shared.responses import fail
from aos.utils.privacy import opaque_digest

_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return current
"""


def _rate_limit_value(dimension: str, value: str) -> str:
    """Hide account/provider identifiers from Redis key names.

    IP keys remain readable for infrastructure troubleshooting. Every other
    Authentication dimension is a deterministic site-keyed digest so normalized
    emails, provider subjects, user ids, or future auth identifiers are not
    exposed in Redis key listings and cannot be dictionary-recovered from a
    bare public hash, while still coordinating consistently across nodes.
    """
    raw = str(value or "").strip()
    if str(dimension or "").strip().lower() == "ip":
        return raw
    return "hmac256_" + opaque_digest(raw)[:48]


def auth_rate_limit(*, operation: str, dimension: str, value: str, limit: int, ttl_seconds: int = 3600, message: str):
    try:
        key = rate_limit_key("auth", operation, dimension, _rate_limit_value(dimension, value))
        cache = frappe.cache()
        # RedisWrapper.eval is inherited from redis-py, so it does not apply
        # Frappe's site prefix automatically. Explicit namespacing prevents two
        # sites sharing Redis from consuming each other's Authentication quota.
        redis_key = cache.make_key(key)
        current = int(cache.eval(_WINDOW_SCRIPT, 1, redis_key, int(ttl_seconds)))
    except Exception as exc:
        from .observability import log_auth_exception

        log_auth_exception("AOS Auth Rate Limit Unavailable", exc, operation=operation)
        return fail("Authentication service temporarily unavailable.", error="SERVICE_UNAVAILABLE")
    if current > int(limit):
        return fail(message, error="RATE_LIMIT", data={"retry_after_seconds": int(ttl_seconds)})
    return None


def auth_ip_limit(*, operation: str, limit: int, message: str, ttl_seconds: int = 3600):
    return auth_rate_limit(
        operation=operation,
        dimension="ip",
        value=request_ip(),
        limit=limit,
        ttl_seconds=ttl_seconds,
        message=message,
    )
