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


def _redis_key(cache, key: str):
    """Return Frappe's site-prefixed Redis key for raw numeric structures.

    Frappe's RedisWrapper serializes values for its high-level hash helpers and
    only prefixes a subset of native redis-py methods. Shorts hot counters must
    stay numeric so HINCRBY remains atomic, therefore this module deliberately
    uses redis-py's raw hash primitives with an explicitly prefixed key.
    """
    make_key = getattr(cache, "make_key", None)
    return make_key(key) if callable(make_key) else key


def _raw_hgetall(cache, key: str):
    return cache.execute_command("HGETALL", _redis_key(cache, key))


def _raw_hsetnx(cache, key: str, field: str, value: int):
    return cache.execute_command("HSETNX", _redis_key(cache, key), field, int(value))


def _raw_hincrby(cache, key: str, field: str, amount: int):
    return cache.execute_command("HINCRBY", _redis_key(cache, key), field, int(amount))


def _raw_expire(cache, key: str, seconds: int):
    return cache.execute_command("EXPIRE", _redis_key(cache, key), int(seconds))


def _dirty_members(cache, limit: int) -> list[Any]:
    """Read at most ``limit`` dirty Short ids without materializing the set.

    RedisWrapper.srandmember currently ignores its count argument, so using it
    for reconciliation silently reduces each run to one member. SSCAN keeps the
    operation bounded while remaining safe for a concurrently changing set.
    """
    maximum = max(1, min(int(limit or 0), 5000))
    redis_key = _redis_key(cache, _DIRTY_KEY)
    cursor = 0
    members: list[Any] = []
    seen: set[str] = set()
    while len(members) < maximum:
        cursor, batch = cache.execute_command(
            "SSCAN",
            redis_key,
            int(cursor or 0),
            "COUNT",
            min(512, maximum - len(members)),
        )
        for raw in batch or []:
            marker = _text(raw)
            if marker in seen:
                continue
            seen.add(marker)
            members.append(raw)
            if len(members) >= maximum:
                break
        if int(cursor or 0) == 0:
            break
    return members


def ensure_seed(short_id: str) -> None:
    cache = frappe.cache()
    key = hot_key(short_id)
    if cache.exists(key):
        return
    row = frappe.db.get_value("AOS Short", short_id, list(_COUNTER_FIELDS), as_dict=True)
    if not row:
        return
    for field in _COUNTER_FIELDS:
        _raw_hsetnx(cache, key, field, max(0, int(row.get(field) or 0)))
    _raw_expire(cache, key, 30 * 86400)


def record_signal(short_id: str, event_type: str, *, watch_ms: int = 0, unique_new: bool = False) -> None:
    cache = frappe.cache()
    ensure_seed(short_id)
    key = hot_key(short_id)
    daily = daily_key(short_id)
    field = _EVENT_FIELD.get(event_type)
    if field:
        _raw_hincrby(cache, key, field, 1)
        _raw_hincrby(cache, daily, field, 1)
    if unique_new:
        _raw_hincrby(cache, key, "unique_viewer_count", 1)
        _raw_hincrby(cache, daily, "unique_viewer_count", 1)
    bounded_watch = max(0, min(int(watch_ms or 0), 600000))
    if bounded_watch:
        _raw_hincrby(cache, key, "watch_time_ms", bounded_watch)
        _raw_hincrby(cache, daily, "watch_time_ms", bounded_watch)
    _raw_expire(cache, key, 30 * 86400)
    _raw_expire(cache, daily, 8 * 86400)
    _raw_hincrby(cache, key, "__dirty_version", 1)
    cache.sadd(_DIRTY_KEY, short_id)


def flush_hot_metrics(*, limit: int = 1000) -> int:
    cache = frappe.cache()
    members = _dirty_members(cache, limit)
    flushed = 0
    cleanups: list[tuple[str, str, int]] = []
    for raw_id in members:
        short_id = _text(raw_id)
        if not frappe.db.exists("AOS Short", short_id):
            clear_short_hot_state(short_id)
            continue
        key = hot_key(short_id)
        values = _decode_hash(_raw_hgetall(cache, key) or {})
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
        _register_after_commit(lambda: _clear_dirty_batch(pending))
    return flushed


def _register_after_commit(callback) -> None:
    """Register a Redis cleanup only after the surrounding DB transaction commits."""
    frappe.db.after_commit.add(callback)


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
    cache.eval(
        script,
        2,
        _redis_key(cache, key),
        _redis_key(cache, _DIRTY_KEY),
        "__dirty_version",
        str(int(version)),
        short_id,
    )


def daily_snapshot(short_id: str, day: str) -> dict[str, int]:
    cache = frappe.cache()
    return _decode_hash(_raw_hgetall(cache, daily_key(short_id, day)) or {})


def clear_short_hot_state(short_id: str) -> None:
    cache = frappe.cache()
    cache.delete_value(hot_key(short_id))
    # Daily hot state is also external to MariaDB transactions. Clearing the
    # current-day key prevents synthetic/test Short ids from surviving a rollback
    # and being reconciled into a later row that reuses the same id.
    cache.delete_value(daily_key(short_id))
    cache.srem(_DIRTY_KEY, short_id)
