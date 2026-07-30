"""Versioned, privacy-safe Redis caching for Maps responses."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe

_CACHE_VERSION = "v3"
_MAX_CACHE_VALUE_BYTES = 1_500_000


def maps_cache_key(namespace: str, payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    safe_namespace = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in str(namespace or "maps")
    )[:40]
    return f"aos:maps:{_CACHE_VERSION}:{safe_namespace}:{digest}"


def get_cached_json(key: str, *, expected_type: type) -> Any | None:
    try:
        cached = frappe.cache().get_value(str(key)[:256])
    except Exception:
        return None
    if cached is None:
        return None
    if isinstance(cached, expected_type):
        return cached
    if isinstance(cached, bytes):
        if len(cached) > _MAX_CACHE_VALUE_BYTES:
            return None
        try:
            cached = cached.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(cached, str):
        return None
    if len(cached.encode("utf-8")) > _MAX_CACHE_VALUE_BYTES:
        return None
    try:
        decoded = json.loads(cached)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, expected_type) else None


def set_cached_json(key: str, payload: Any, *, ttl_seconds: int) -> None:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        if len(encoded.encode("utf-8")) > _MAX_CACHE_VALUE_BYTES:
            return
        frappe.cache().set_value(
            str(key)[:256],
            encoded,
            expires_in_sec=max(1, int(ttl_seconds)),
        )
    except Exception:
        pass
