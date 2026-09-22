"""Redis-backed high-frequency Live state.

Durable Live lifecycle, comments and view-session history remain in MariaDB.
Reaction taps are ephemeral UI events: persisting one SQL row per tap creates
unbounded write amplification, so new taps are accumulated atomically in Redis
and periodically materialized into ``AOS Live Stream.reaction_count``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import frappe

REACTION_STATE_TTL_SECONDS = 48 * 60 * 60
REACTION_DB_FLUSH_EVERY = 25
REACTION_PUBLISH_LIMIT_PER_SECOND = 20
VIEWER_JOIN_PUBLISH_LIMIT_PER_SECOND = 10


def _live_digest(live_id: str) -> str:
    return hashlib.sha256(str(live_id or "").encode("utf-8")).hexdigest()[:24]




HOT_MATERIALIZE_GATE_SECONDS = 1


def schedule_hot_counter_materialization(*, live_id: str) -> None:
    """Coalesce Redis hot-counter flushes to about one job/live/second.

    The job is enqueued after the caller transaction commits, so participant
    requests never upgrade their shared lifecycle lock into a write on the
    parent Live row. Redis degradation falls back to enqueueing one best-effort
    flush; the five-minute reconciler remains the final recovery path.
    """
    if not live_id:
        return
    digest = _live_digest(live_id)
    should_enqueue = True
    try:
        cache = frappe.cache()
        gate_key = cache.make_key(f"aos:live:hot-materialize:v1:{digest}")
        sequence = _as_int(cache.incr(gate_key))
        try:
            cache.expire(gate_key, HOT_MATERIALIZE_GATE_SECONDS)
        except Exception:
            pass
        should_enqueue = sequence <= 1
    except Exception:
        should_enqueue = True

    if not should_enqueue:
        return

    try:
        frappe.enqueue(
            "aos.tasks.live.materialize_hot_live_counters",
            queue="short",
            enqueue_after_commit=True,
            job_id=f"aos_live_hot:{digest}:{int(time.time())}",
            live_id=live_id,
            delay_seconds=0.35,
        )
    except Exception:
        # Durable comments/view rows plus periodic reconciliation make this
        # safe to recover when the queue is temporarily unavailable.
        pass

def _reaction_key(live_id: str) -> str:
    return f"aos:live:reaction:v2:{_live_digest(live_id)}"


def _publish_key(live_id: str) -> str:
    return f"aos:live:reaction-publish:v1:{_live_digest(live_id)}"


def _as_int(value, default: int = 0) -> int:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return default
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default



_REACTION_INCREMENT_LUA = """
if redis.call('HEXISTS', KEYS[1], 'base') == 0 then
  redis.call('HSET', KEYS[1], 'base', ARGV[1], 'delta_total', 0)
end
local delta = redis.call('HINCRBY', KEYS[1], 'delta_total', 1)
redis.call('HINCRBY', KEYS[1], 'type:' .. ARGV[2], 1)
redis.call('EXPIRE', KEYS[1], ARGV[3])
local base = tonumber(redis.call('HGET', KEYS[1], 'base') or '0')
return {base + delta, delta}
"""

@dataclass(frozen=True)
class ReactionIncrement:
    total: int
    delta: int
    should_flush: bool


def _initial_reaction_base(live_id: str) -> int:
    """Return the durable materialized total used to seed Redis.

    Reaction taps are no longer persisted as one SQL row per event. The Live
    row is the sole durable aggregate, so Redis recovery never scans an event
    table on a hot-room request path.
    """
    materialized = _as_int(
        frappe.db.get_value("AOS Live Stream", live_id, "reaction_count")
    )
    return max(materialized, 0)


def increment_reaction(*, live_id: str, reaction_type: str) -> ReactionIncrement | None:
    """Atomically accumulate one reaction; return ``None`` if Redis is unavailable."""
    if not live_id or not reaction_type:
        return None
    try:
        cache = frappe.cache()
        key = cache.make_key(_reaction_key(live_id))

        # Frappe's RedisWrapper pickles hset/hget values and its inherited
        # Redis numeric/Lua commands do not add the site prefix. Hot Live
        # hashes therefore use one consistent raw Redis representation on an
        # explicitly site-namespaced key. Never mix RedisWrapper.hset/hget
        # with HINCRBY/EVAL on these hashes.
        base_raw = cache.execute_command("HGET", key, "base")
        base = (
            _initial_reaction_base(live_id)
            if base_raw is None
            else _as_int(base_raw)
        )
        values = cache.eval(
            _REACTION_INCREMENT_LUA,
            1,
            key,
            max(0, int(base or 0)),
            reaction_type,
            REACTION_STATE_TTL_SECONDS,
        )
        if not values or len(values) < 2:
            return None
        total = max(0, _as_int(values[0]))
        delta = max(0, _as_int(values[1]))
        return ReactionIncrement(
            total=total,
            delta=delta,
            should_flush=delta == 1 or delta % REACTION_DB_FLUSH_EVERY == 0,
        )
    except Exception:
        return None


def current_reaction_total(*, live_id: str) -> int | None:
    """Return the Redis-backed reaction total when an accumulator exists."""
    if not live_id:
        return None
    try:
        cache = frappe.cache()
        key = cache.make_key(_reaction_key(live_id))
        raw = cache.execute_command("HGETALL", key) or {}
        if not raw:
            return None
        base = _as_int(raw.get(b"base", raw.get("base")))
        delta = _as_int(raw.get(b"delta_total", raw.get("delta_total")))
        return max(0, base + delta)
    except Exception:
        return None


def should_publish_reaction(*, live_id: str) -> bool:
    """Sample room fan-out while still accepting/counting every reaction tap."""
    if not live_id:
        return False
    try:
        cache = frappe.cache()
        key = cache.make_key(_publish_key(live_id))
        count = _as_int(cache.incr(key))
        try:
            cache.expire(key, 1)
        except Exception:
            pass
        return count <= REACTION_PUBLISH_LIMIT_PER_SECOND
    except Exception:
        # Redis degradation must not make Live reactions unusable.
        return True


def clear_reaction_state(*, live_id: str) -> None:
    try:
        cache = frappe.cache()
        cache.delete(
            cache.make_key(_reaction_key(live_id)),
            cache.make_key(_publish_key(live_id)),
        )
    except Exception:
        pass


def should_publish_viewer_joined(*, live_id: str) -> bool:
    """Protect the host UI from one targeted event per entrant in join storms."""
    if not live_id:
        return False
    try:
        cache = frappe.cache()
        key = cache.make_key(
            f"aos:live:viewer-joined-publish:v1:{_live_digest(live_id)}"
        )
        count = _as_int(cache.incr(key))
        try:
            cache.expire(key, 1)
        except Exception:
            pass
        return count <= VIEWER_JOIN_PUBLISH_LIMIT_PER_SECOND
    except Exception:
        return True


VIEW_STATE_TTL_SECONDS = 48 * 60 * 60
COMMENT_STATE_TTL_SECONDS = 48 * 60 * 60


def _view_key(live_id: str) -> str:
    return f"aos:live:view-metrics:v1:{_live_digest(live_id)}"


def _comment_key(live_id: str) -> str:
    return f"aos:live:comment-count:v1:{_live_digest(live_id)}"


_VIEW_JOIN_LUA = """
if redis.call('HEXISTS', KEYS[1], 'initialized') == 0 then
  redis.call('HSET', KEYS[1],
    'initialized', 1,
    'viewer_count', ARGV[1],
    'total_views', ARGV[2],
    'total_joins', ARGV[3],
    'unique_viewers', ARGV[4],
    'peak_viewers', ARGV[5],
    'total_watch_time_seconds', ARGV[6])
end
local viewers = redis.call('HINCRBY', KEYS[1], 'viewer_count', 1)
local joins = redis.call('HINCRBY', KEYS[1], 'total_joins', 1)
redis.call('HSET', KEYS[1], 'total_views', joins)
local peak = tonumber(redis.call('HGET', KEYS[1], 'peak_viewers') or '0')
if viewers > peak then
  peak = viewers
  redis.call('HSET', KEYS[1], 'peak_viewers', peak)
end
redis.call('EXPIRE', KEYS[1], ARGV[7])
return {viewers, joins, joins,
  tonumber(redis.call('HGET', KEYS[1], 'unique_viewers') or '0'),
  peak,
  tonumber(redis.call('HGET', KEYS[1], 'total_watch_time_seconds') or '0')}
"""

_VIEW_LEAVE_LUA = """
if redis.call('HEXISTS', KEYS[1], 'initialized') == 0 then
  redis.call('HSET', KEYS[1],
    'initialized', 1,
    'viewer_count', ARGV[1],
    'total_views', ARGV[2],
    'total_joins', ARGV[3],
    'unique_viewers', ARGV[4],
    'peak_viewers', ARGV[5],
    'total_watch_time_seconds', ARGV[6])
end
local viewers = tonumber(redis.call('HGET', KEYS[1], 'viewer_count') or '0')
viewers = viewers - tonumber(ARGV[7])
if viewers < 0 then viewers = 0 end
redis.call('HSET', KEYS[1], 'viewer_count', viewers)
local watch = redis.call('HINCRBY', KEYS[1], 'total_watch_time_seconds', ARGV[8])
redis.call('EXPIRE', KEYS[1], ARGV[9])
return {viewers,
  tonumber(redis.call('HGET', KEYS[1], 'total_views') or '0'),
  tonumber(redis.call('HGET', KEYS[1], 'total_joins') or '0'),
  tonumber(redis.call('HGET', KEYS[1], 'unique_viewers') or '0'),
  tonumber(redis.call('HGET', KEYS[1], 'peak_viewers') or '0'),
  watch}
"""


def _view_result(values) -> dict[str, int] | None:
    if not values or len(values) < 6:
        return None
    return {
        "viewer_count": max(0, _as_int(values[0])),
        "total_views": max(0, _as_int(values[1])),
        "total_joins": max(0, _as_int(values[2])),
        "unique_viewers": max(0, _as_int(values[3])),
        "peak_viewers": max(0, _as_int(values[4])),
        "total_watch_time_seconds": max(0, _as_int(values[5])),
    }


def increment_view_metrics(*, live_id: str, base: dict[str, int]) -> dict[str, int] | None:
    try:
        cache = frappe.cache()
        values = cache.eval(
            _VIEW_JOIN_LUA,
            1,
            cache.make_key(_view_key(live_id)),
            int(base.get("viewer_count") or 0),
            int(base.get("total_views") or 0),
            int(base.get("total_joins") or 0),
            int(base.get("unique_viewers") or 0),
            int(base.get("peak_viewers") or 0),
            int(base.get("total_watch_time_seconds") or 0),
            VIEW_STATE_TTL_SECONDS,
        )
        return _view_result(values)
    except Exception:
        return None


def decrement_view_metrics(
    *,
    live_id: str,
    base: dict[str, int],
    count: int = 1,
    watch_duration_seconds: int = 0,
) -> dict[str, int] | None:
    try:
        cache = frappe.cache()
        values = cache.eval(
            _VIEW_LEAVE_LUA,
            1,
            cache.make_key(_view_key(live_id)),
            int(base.get("viewer_count") or 0),
            int(base.get("total_views") or 0),
            int(base.get("total_joins") or 0),
            int(base.get("unique_viewers") or 0),
            int(base.get("peak_viewers") or 0),
            int(base.get("total_watch_time_seconds") or 0),
            max(0, int(count or 0)),
            max(0, int(watch_duration_seconds or 0)),
            VIEW_STATE_TTL_SECONDS,
        )
        return _view_result(values)
    except Exception:
        return None


def current_view_metrics(*, live_id: str) -> dict[str, int] | None:
    try:
        cache = frappe.cache()
        raw = cache.execute_command("HGETALL", cache.make_key(_view_key(live_id))) or {}
        if not raw:
            return None
        def field(name: str) -> int:
            return max(0, _as_int(raw.get(name, raw.get(name.encode("utf-8")))))
        return {
            "viewer_count": field("viewer_count"),
            "total_views": field("total_views"),
            "total_joins": field("total_joins"),
            "unique_viewers": field("unique_viewers"),
            "peak_viewers": field("peak_viewers"),
            "total_watch_time_seconds": field("total_watch_time_seconds"),
        }
    except Exception:
        return None


def clear_view_metrics(*, live_id: str) -> None:
    try:
        cache = frappe.cache()
        cache.delete(cache.make_key(_view_key(live_id)))
    except Exception:
        pass


_COMMENT_CHANGE_LUA = """
if redis.call('HEXISTS', KEYS[1], 'initialized') == 0 then
  redis.call('HSET', KEYS[1], 'initialized', 1, 'count', ARGV[1])
end
local value = tonumber(redis.call('HGET', KEYS[1], 'count') or '0') + tonumber(ARGV[2])
if value < 0 then value = 0 end
redis.call('HSET', KEYS[1], 'count', value)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return value
"""


def change_comment_count(*, live_id: str, base: int, delta: int) -> int | None:
    try:
        cache = frappe.cache()
        value = cache.eval(
            _COMMENT_CHANGE_LUA,
            1,
            cache.make_key(_comment_key(live_id)),
            max(0, int(base or 0)),
            int(delta or 0),
            COMMENT_STATE_TTL_SECONDS,
        )
        return max(0, _as_int(value))
    except Exception:
        return None


def current_comment_count(*, live_id: str) -> int | None:
    try:
        cache = frappe.cache()
        raw = cache.execute_command(
            "HGET", cache.make_key(_comment_key(live_id)), "count"
        )
        return None if raw is None else max(0, _as_int(raw))
    except Exception:
        return None


def set_comment_count(*, live_id: str, count: int) -> None:
    try:
        cache = frappe.cache()
        key = cache.make_key(_comment_key(live_id))
        cache.execute_command(
            "HSET",
            key,
            "initialized",
            1,
            "count",
            max(0, int(count or 0)),
        )
        cache.expire(key, COMMENT_STATE_TTL_SECONDS)
    except Exception:
        pass


def clear_comment_state(*, live_id: str) -> None:
    try:
        cache = frappe.cache()
        cache.delete(cache.make_key(_comment_key(live_id)))
    except Exception:
        pass
