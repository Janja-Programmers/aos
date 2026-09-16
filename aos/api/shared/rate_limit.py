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

_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return current
"""


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
	"""Atomically increment a site-scoped shared Redis counter with a fixed TTL."""

	cache = frappe.cache()
	safe_key = str(key or "").strip()[:512]
	if not safe_key:
		raise ValueError("Rate-limit key is required.")
	ttl = max(1, int(ttl_seconds))
	# RedisWrapper.eval does not apply Frappe's site prefix automatically.
	# Explicit namespacing keeps counters isolated when sites share Redis.
	redis_key = cache.make_key(safe_key)
	return int(cache.eval(_WINDOW_SCRIPT, 1, redis_key, ttl))


def rate_limit(key: str, ttl_seconds: int, limit: int, message: str):
	"""Return fail(...) when rate limit exceeded, otherwise None."""

	if cache_incr(key, ttl_seconds) > int(limit):
		try:
			from aos.utils.metrics import record_rate_limit_rejection

			record_rate_limit_rejection("application")
		except Exception:
			pass
		return fail(message, error="RATE_LIMIT")
	return None
