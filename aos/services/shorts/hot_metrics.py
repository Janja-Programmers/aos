"""Bounded Redis hot counters for high-frequency Shorts playback signals.

Redis values are absolute cumulative counters seeded from the durable Short row,
not flush deltas. Reconciliation can therefore be retried safely with monotonic
``GREATEST`` updates and cannot double-apply a batch after a worker crash.
"""
from __future__ import annotations

from datetime import date
from typing import Any
import frappe

_HOT_PREFIX = "aos:shorts:hot:"
_DAILY_PREFIX = "aos:shorts:daily:"
_DIRTY_KEY = "aos:shorts:hot:dirty"
_COUNTER_FIELDS = (
    "impression_count", "view_count", "unique_viewer_count", "watch_time_ms",
    "completion_count", "rewatch_count", "early_skip_count",
)
_EVENT_FIELD = {
    "impression": "impression_count",
    "qualified_view": "view_count",
    "complete": "completion_count",
    "rewatch": "rewatch_count",
    "early_skip": "early_skip_count",
}


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def _decode_hash(value: dict[Any, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, raw in (value or {}).items():
        try:
            out[_text(key)] = max(0, int(_text(raw)))
        except (TypeError, ValueError):
            continue
    return out


def hot_key(short_id: str) -> str:
    return f"{_HOT_PREFIX}{short_id}"


def daily_key(short_id: str, day: str | None = None) -> str:
    return f"{_DAILY_PREFIX}{day or date.today().isoformat()}:{short_id}"


def ensure_seed(short_id: str) -> None:
    cache = frappe.cache()
    key = hot_key(short_id)
    if cache.exists(key):
        return
    row = frappe.db.get_value("AOS Short", short_id, list(_COUNTER_FIELDS), as_dict=True)
    if not row:
        return
    for field in _COUNTER_FIELDS:
        cache.hsetnx(key, field, max(0, int(row.get(field) or 0)))
    cache.expire(key, 30 * 86400)


def record_signal(short_id: str, event_type: str, *, watch_ms: int = 0, unique_new: bool = False) -> None:
    cache = frappe.cache()
    ensure_seed(short_id)
    key = hot_key(short_id)
    daily = daily_key(short_id)
    field = _EVENT_FIELD.get(event_type)
    if field:
        cache.hincrby(key, field, 1)
        cache.hincrby(daily, field, 1)
    if unique_new:
        cache.hincrby(key, "unique_viewer_count", 1)
        cache.hincrby(daily, "unique_viewer_count", 1)
    bounded_watch = max(0, min(int(watch_ms or 0), 600000))
    if bounded_watch:
        cache.hincrby(key, "watch_time_ms", bounded_watch)
        cache.hincrby(daily, "watch_time_ms", bounded_watch)
    cache.expire(key, 30 * 86400)
    cache.expire(daily, 8 * 86400)
    cache.hincrby(key, "__dirty_version", 1)
    cache.sadd(_DIRTY_KEY, short_id)


def flush_hot_metrics(*, limit: int = 1000) -> int:
    cache = frappe.cache()
    try:
        members = cache.srandmember(_DIRTY_KEY, number=max(1, min(int(limit), 5000))) or []
    except TypeError:
        members = list(cache.smembers(_DIRTY_KEY) or [])[: max(1, min(int(limit), 5000))]
    if isinstance(members, (str, bytes)):
        members = [members]
    flushed = 0
    cleanups: list[tuple[str, str, int]] = []
    for raw_id in members:
        short_id = _text(raw_id)
        if not frappe.db.exists("AOS Short", short_id):
            clear_short_hot_state(short_id)
            continue
        key = hot_key(short_id)
        values = _decode_hash(cache.hgetall(key) or {})
        if not values:
            cache.srem(_DIRTY_KEY, short_id)
            continue
        version = int(values.get("__dirty_version") or 0)
        assignments = []
        params: list[Any] = []
        for field in _COUNTER_FIELDS:
            if field in values:
                assignments.append(f"`{field}`=GREATEST(COALESCE(`{field}`,0),%s)")
                params.append(values[field])
        if assignments:
            params.append(short_id)
            frappe.db.sql(f"UPDATE `tabAOS Short` SET {','.join(assignments)} WHERE name=%s", tuple(params))
            flushed += 1
            cleanups.append((key, short_id, version))
    if cleanups:
        pending = tuple(cleanups)
        frappe.db.after_commit.add(lambda: _clear_dirty_batch(pending))
    return flushed



def _clear_dirty_batch(items: tuple[tuple[str, str, int], ...]) -> None:
    cache = frappe.cache()
    for key, short_id, version in items:
        _clear_dirty_if_unchanged(cache, key=key, short_id=short_id, version=version)


def _clear_dirty_if_unchanged(cache, *, key: str, short_id: str, version: int) -> None:
    """Remove a flushed Short from the dirty set only if no writer raced the flush."""
    script = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
if current and tostring(current) == tostring(ARGV[2]) then
  return redis.call('SREM', KEYS[2], ARGV[3])
end
return 0
"""
    client = getattr(cache, "redis_server", None) or getattr(cache, "_redis", None) or cache
    client.eval(script, 2, key, _DIRTY_KEY, "__dirty_version", str(int(version)), short_id)

def daily_snapshot(short_id: str, day: str) -> dict[str, int]:
    return _decode_hash(frappe.cache().hgetall(daily_key(short_id, day)) or {})


def clear_short_hot_state(short_id: str) -> None:
    cache = frappe.cache()
    cache.delete_value(hot_key(short_id))
    # Daily hot state is also external to MariaDB transactions. Clearing the
    # current-day key prevents synthetic/test Short ids from surviving a rollback
    # and being reconciled into a later row that reuses the same id.
    cache.delete_value(daily_key(short_id))
    cache.srem(_DIRTY_KEY, short_id)
