"""Shared throttling policy for public Localization reads."""

from __future__ import annotations

from aos.api.shared.auth import session_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip


def localization_rate_limit(*, endpoint: str, limit: int, message: str):
	"""Apply the shared Redis limiter without making Redis a read-path dependency.

	Authenticated callers are isolated by user; guests are isolated by client IP.
	If the shared limiter is temporarily unavailable, fail open here so static
	localization reads can still fall back to their database source of truth.
	Edge/reverse-proxy rate limits remain the outage-time abuse backstop.
	"""

	user = session_user()
	if user and user != "Guest":
		scope, subject = "user", user
	else:
		scope, subject = "ip", request_ip()
	try:
		return rate_limit(
			key=rate_limit_key("localization", endpoint, scope, subject),
			ttl_seconds=60,
			limit=limit,
			message=message,
		)
	except Exception:
		return None
