"""Canonical persistence and cache helpers for per-user market preferences."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

USER_PREFERENCE_CACHE_SCHEMA = "v1"
USER_PREFERENCE_CACHE_TTL_SECONDS = 300


def _cache_key(user: str) -> str:
	digest = hashlib.sha256(user.encode("utf-8")).hexdigest()
	return f"aos:user_pref:v2:{digest}"


def clear_user_preference_cache(user: str | None) -> None:
	"""Invalidate one user's cached preference without failing the request."""

	user = str(user or "").strip()
	if not user:
		return

	try:
		frappe.cache().delete_value(_cache_key(user))
	except Exception:
		pass


def clear_user_preference_cache_for_doc(doc: Any, _method: str | None = None) -> None:
	"""Frappe document-event adapter for cache invalidation."""

	clear_user_preference_cache(getattr(doc, "user", None))


def get_user_preference(user: str, *, use_cache: bool = True):
	"""Return the canonical preference row for an authenticated user.

	The cache contains only non-sensitive canonical IDs. A ``frappe._dict`` is
	returned so existing callers may use either attribute or mapping access.
	"""

	user = str(user or "").strip()
	if not user or user == "Guest":
		return None

	cache = None
	try:
		cache = frappe.cache()
	except Exception:
		pass
	key = _cache_key(user)

	if use_cache and cache is not None:
		try:
			cached = cache.get_value(key)
			if isinstance(cached, dict) and cached.get("_schema") == USER_PREFERENCE_CACHE_SCHEMA:
				payload = dict(cached)
				payload.pop("_schema", None)
				return frappe._dict(payload)
		except Exception:
			pass

	preference = frappe.db.get_value(
		"AOS User Preference",
		{"user": user},
		["name", "user", "country", "currency", "language"],
		as_dict=True,
	)
	if not preference:
		return None

	payload = {
		"name": preference.name,
		"user": preference.user,
		"country": preference.country,
		"currency": preference.currency,
		"language": preference.language,
	}

	if cache is not None:
		try:
			cache.set_value(
				key,
				{"_schema": USER_PREFERENCE_CACHE_SCHEMA, **payload},
				expires_in_sec=USER_PREFERENCE_CACHE_TTL_SECONDS,
			)
		except Exception:
			pass

	return frappe._dict(payload)


def get_user_preference_for_update(user: str):
	"""Lock and return the current preference row for a partial update.

	Serializing preference updates prevents concurrent independent field writes
	from overwriting each other with stale values.
	"""

	user = str(user or "").strip()
	if not user or user == "Guest":
		return None

	rows = frappe.db.sql(
		"""
		SELECT name, user, country, currency, language
		FROM `tabAOS User Preference`
		WHERE user = %s
		LIMIT 1
		FOR UPDATE
		""",
		(user,),
		as_dict=True,
	)
	return rows[0] if rows else None


def is_country_locked(user: str) -> bool:
	"""Return whether seller activity makes the account market immutable."""

	user = str(user or "").strip()
	if not user or user == "Guest":
		return False

	seller = frappe.db.get_value("AOS Seller", {"user": user}, "name")
	return bool(seller and frappe.db.exists("AOS Ad", {"seller": seller}))
