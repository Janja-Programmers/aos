"""Redis-backed cache helpers for immutable-by-response Catalog reference data.

The database remains authoritative. Cache failures are deliberately non-fatal.
Invalidation happens immediately and again after commit so a concurrent reader
cannot repopulate pre-commit data and leave it stale after a successful write.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import frappe

CATALOG_CACHE_SCHEMA = "v3"
CATALOG_CACHE_TTL_SECONDS = 300
_CATEGORY_TREE_KEY = f"aos:catalog:{CATALOG_CACHE_SCHEMA}:categories"
_SCHEMA_PREFIX = f"aos:catalog:{CATALOG_CACHE_SCHEMA}:schema:"


def _schema_key(category_id: str) -> str:
    digest = hashlib.sha256(str(category_id or "").encode("utf-8")).hexdigest()
    return f"{_SCHEMA_PREFIX}{digest}"


def _get(key: str, *, payload_type: type):
    try:
        cached = frappe.cache().get_value(key)
    except Exception:
        return None
    if not isinstance(cached, dict) or cached.get("_schema") != CATALOG_CACHE_SCHEMA:
        return None
    payload = cached.get("payload")
    if not isinstance(payload, payload_type):
        return None
    return copy.deepcopy(payload)


def _set(key: str, payload: Any) -> None:
    try:
        frappe.cache().set_value(
            key,
            {"_schema": CATALOG_CACHE_SCHEMA, "payload": copy.deepcopy(payload)},
            expires_in_sec=CATALOG_CACHE_TTL_SECONDS,
        )
    except Exception:
        return


def get_category_tree_cache() -> list[dict[str, Any]] | None:
    return _get(_CATEGORY_TREE_KEY, payload_type=list)


def set_category_tree_cache(tree: list[dict[str, Any]]) -> None:
    _set(_CATEGORY_TREE_KEY, tree)


def get_category_schema_cache(category_id: str) -> dict[str, Any] | None:
    return _get(_schema_key(category_id), payload_type=dict)


def set_category_schema_cache(category_id: str, schema: dict[str, Any]) -> None:
    _set(_schema_key(category_id), schema)


def _delete_catalog_cache() -> None:
    try:
        cache = frappe.cache()
    except Exception:
        return
    try:
        cache.delete_value(_CATEGORY_TREE_KEY)
    except Exception:
        pass
    try:
        cache.delete_keys(f"{_SCHEMA_PREFIX}*")
    except Exception:
        pass


def clear_catalog_cache() -> None:
    """Invalidate now and post-commit to close stale cache refill races."""

    _delete_catalog_cache()
    manager = getattr(frappe.db, "after_commit", None)
    if manager is not None and hasattr(manager, "add"):
        manager.add(_delete_catalog_cache)
