"""Shared Redis cache helpers for localization reference data.

The database remains authoritative. Cache failures are deliberately non-fatal.
Invalidation happens both immediately and after commit: the immediate delete
prevents this transaction from serving an old value, while the post-commit
delete closes the race where another worker repopulates old data before the
writer commits.
"""

from __future__ import annotations

import copy
from typing import Any

import frappe

from .constants import (
	LOCALIZATION_BUNDLE_CACHE_KEY,
	LOCALIZATION_CACHE_SCHEMA,
	LOCALIZATION_CACHE_TTL_SECONDS,
	LOCALIZATION_DEFAULTS_CACHE_KEY,
)

_CACHE_KEYS = (LOCALIZATION_DEFAULTS_CACHE_KEY, LOCALIZATION_BUNDLE_CACHE_KEY)


def cache_get(key: str):
	try:
		cached = frappe.cache().get_value(key)
		if isinstance(cached, dict) and cached.get("_schema") == LOCALIZATION_CACHE_SCHEMA:
			payload = copy.deepcopy(cached)
			payload.pop("_schema", None)
			return payload
	except Exception:
		return None
	return None


def cache_set(key: str, payload: dict[str, Any]) -> None:
	try:
		frappe.cache().set_value(
			key,
			{"_schema": LOCALIZATION_CACHE_SCHEMA, **copy.deepcopy(payload)},
			expires_in_sec=LOCALIZATION_CACHE_TTL_SECONDS,
		)
	except Exception:
		return


def _delete_cache_keys() -> None:
	try:
		cache = frappe.cache()
	except Exception:
		return
	for key in _CACHE_KEYS:
		try:
			cache.delete_value(key)
		except Exception:
			continue


def clear_localization_cache(*_args: Any, **_kwargs: Any) -> None:
	"""Invalidate localization cache now and again after a successful commit."""

	_delete_cache_keys()
	manager = getattr(frappe.db, "after_commit", None)
	if manager is None or not hasattr(manager, "add"):
		return
	# Registering duplicate deletes is harmless and safer than carrying request-local
	# deduplication state across a rollback followed by another transaction in the
	# same worker/request. Correctness does not depend on process-local flags.
	manager.add(_delete_cache_keys)


def localization_master_changed(_doc: Any = None, _method: str | None = None) -> None:
	clear_localization_cache()
