"""Durable companion work results and independently retryable callbacks.

The Redis result record is the source of truth after external work completes.
Work and callback delivery use separate deterministic RQ jobs so callback
transport failures never rerun the external side effect.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rq import Retry, get_current_job

logger = logging.getLogger(__name__)

_ACTIVE_JOB_STATUSES = {"queued", "started", "deferred", "scheduled"}
_TERMINAL_WORK_STATES = {"work_complete", "work_failed"}
_CALLBACK_ACTIVE_STATES = {"pending", "retrying", "dead_letter"}
_ALLOWED_PHASES = {
    "accepted",
    "starting",
    "downloading",
    "processing",
    "uploading",
    "provider_request",
    "indexing",
    "ingesting",
    "result_persisted",
    "callback_pending",
    "callback_complete",
    "outcome_uncertain",
    "work_retrying",
    "manual_review",
}
_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
_SAFE_RQ_JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_TERMINAL_SUPERSEDED_CODES = {
    "OLD_GENERATION_CALLBACK",
    "NEWER_DISPATCH_GENERATION",
    "TERMINAL_CALLBACK_CONFLICT",
    "CALLBACK_SUPERSEDED",
}
_TOKEN_MISMATCH_CODES = {"CALLBACK_TOKEN_MISMATCH", "STABLE_DISPATCH_MISMATCH"}
_RETRYABLE_CALLBACK_CODES = {
    "CALLBACK_STATE_CONFLICT",
    "OUTBOX_LEASE_CONFLICT",
    "CALLBACK_TRANSACTION_RETRY",
}


class DurableLifecycleError(RuntimeError):
    pass


class CallbackDeliveryRetryableError(DurableLifecycleError):
    pass


class RetryableWorkError(DurableLifecycleError):
    """Explicitly retryable external-work failure."""


class TerminalWorkError(DurableLifecycleError):
    """Explicitly terminal external-work failure."""


class ProviderOutcomeUncertainError(DurableLifecycleError):
    """The provider may have accepted the side effect; automatic resend is unsafe."""


@dataclass(frozen=True)
class DispatchDecision:
    job: Any
    outcome: str
    authoritative_generation: int
    work_state: str
    callback_state: str
    terminal_result_type: str = ""
    result_digest: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _epoch() -> int:
    return int(time.time())


def _clean(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _stable_id(payload: dict[str, Any]) -> str:
    stable_id = _clean(payload.get("idempotency_key") or payload.get("job_id"), limit=200)
    if not stable_id:
        raise DurableLifecycleError("A stable work identifier is required.")
    return stable_id


def _digest(stable_id: str) -> str:
    return hashlib.sha256(stable_id.encode("utf-8")).hexdigest()


def _result_key(service_type: str, stable_id: str) -> str:
    return f"aos:worker-result:{service_type}:{_digest(stable_id)}"


def _work_lock_key(service_type: str, stable_id: str) -> str:
    return f"aos:worker-lock:{service_type}:{_digest(stable_id)}"


def _callback_lock_key(service_type: str, stable_id: str) -> str:
    return f"aos:callback-lock:{service_type}:{_digest(stable_id)}"


def _callback_pending_key(service_type: str) -> str:
    return f"aos:worker:callback-pending:{service_type}"


def _heartbeat_key(service_type: str) -> str:
    return f"aos:worker:heartbeat:{service_type}"


def _metrics_key(service_type: str) -> str:
    return f"aos:worker:metrics:{service_type}"


def _history_key(service_type: str, stable_id: str, replay_generation: int) -> str:
    return f"aos:worker-history:{service_type}:{_digest(stable_id)}:r{max(1, int(replay_generation))}"


def _stale_history_key(service_type: str, stable_id: str, replay_count: int) -> str:
    return f"aos:worker-history:{service_type}:{_digest(stable_id)}:stale{max(1, int(replay_count))}"


def _decode_map(raw: dict[Any, Any]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw.items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        v = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        decoded[k] = v
    return decoded


_RESULT_PAYLOAD_MAX_CHARS = 65536
_RESULT_STRING_MAX_CHARS = 16000


def _safe_payload(value: Any, *, depth: int = 0, string_limit: int = _RESULT_STRING_MAX_CHARS) -> Any:
    """Bound terminal callback data without retaining arbitrary external bodies."""
    if depth > 5:
        return "[bounded]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[: max(0, int(string_limit))]
    if isinstance(value, list):
        return [
            _safe_payload(item, depth=depth + 1, string_limit=string_limit)
            for item in value[:100]
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key in list(value.keys())[:80]:
            key = _clean(raw_key, limit=100)
            if not key:
                continue
            result[key] = _safe_payload(
                value[raw_key],
                depth=depth + 1,
                string_limit=string_limit,
            )
        return result
    return _clean(value, limit=240)


def _json(value: Any) -> str:
    """Serialize terminal data as valid JSON without silently clipping normal metadata."""

    def render(string_limit: int) -> str:
        return json.dumps(
            _safe_payload(value, string_limit=string_limit),
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    rendered = render(_RESULT_STRING_MAX_CHARS)
    if len(rendered) <= _RESULT_PAYLOAD_MAX_CHARS:
        return rendered

    # Reduce only string values until the complete JSON fits. This preserves the
    # object shape and, unlike slicing serialized JSON, always stores valid JSON.
    low, high = 0, _RESULT_STRING_MAX_CHARS
    best = render(0)
    if len(best) > _RESULT_PAYLOAD_MAX_CHARS:
        safe = _safe_payload(value, string_limit=240)
        if isinstance(safe, dict):
            essential = {
                key: safe[key]
                for key in ("job_id", "status", "error")
                if key in safe
            }
            essential["result_truncated"] = True
            return json.dumps(essential, separators=(",", ":"), sort_keys=True, default=str)
        return json.dumps({"result_truncated": True}, separators=(",", ":"), sort_keys=True)

    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if len(candidate) <= _RESULT_PAYLOAD_MAX_CHARS:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _parse_json(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return default


def _record_metric(redis: Any, service_type: str, event: str, amount: int = 1) -> None:
    allowed = {
        "work_queued",
        "work_started",
        "work_completed",
        "work_failed",
        "result_persisted",
        "callback_queued",
        "callback_retried",
        "callback_completed",
        "callback_dead_lettered",
        "dispatch_uncertain",
        "reconciliation_attempt",
        "duplicate_active",
        "work_result_replay",
        "side_effect_dedupe_hit",
        "callback_token_mismatch",
        "callback_superseded",
        "work_retryable_error",
        "work_retry_exhausted",
        "terminal_work_failure",
        "uncertainty_resolved",
        "manual_review",
        "durable_ttl_repaired",
        "stale_index_cleaned",
        "stale_work_replay",
        "stale_work_terminal_failure",
    }
    if event in allowed:
        redis.hincrby(_metrics_key(service_type), event, max(1, int(amount or 1)))


def load_result(redis: Any, service_type: str, stable_id: str) -> dict[str, str]:
    return _decode_map(redis.hgetall(_result_key(service_type, stable_id)) or {})


def _record_value(name: str, value: Any) -> str:
    # Terminal callback payloads are already bounded by _safe_payload(), but a
    # terminal callback can legitimately exceed 4 KiB (for example when it carries
    # processing metadata). Truncating JSON makes it unparsable and previously
    # caused callbacks to be delivered without job_id/status. Preserve the
    # complete bounded JSON while keeping ordinary diagnostic fields small.
    limit = 65536 if name == "result_payload" else 4000
    return _clean(value, limit=limit)


def _write_record(
    redis: Any,
    service_type: str,
    stable_id: str,
    mapping: dict[str, Any],
    *,
    retention_seconds: int,
) -> None:
    key = _result_key(service_type, stable_id)
    normalized = {
        name: _record_value(name, value)
        for name, value in mapping.items()
        if value is not None
    }
    desired_ttl = max(3600, int(retention_seconds))
    try:
        existing_ttl = int(redis.ttl(key) or -1)
    except Exception:
        existing_ttl = -1
    effective_ttl = max(desired_ttl, existing_ttl if existing_ttl > 0 else 0)
    pipe = redis.pipeline(transaction=True)
    if normalized:
        pipe.hset(key, mapping=normalized)
    pipe.expire(key, effective_ttl)
    pipe.execute()
    if existing_ttl > 0 and effective_ttl > desired_ttl:
        _record_metric(redis, service_type, "durable_ttl_repaired")


def heartbeat_work(
    redis: Any,
    *,
    service_type: str,
    stable_id: str,
    generation: int,
    phase: str,
    retention_seconds: int,
) -> None:
    safe_phase = phase if phase in _ALLOWED_PHASES else "processing"
    now_epoch = _epoch()
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "heartbeat_at": _now(),
            "heartbeat_epoch": now_epoch,
            "phase": safe_phase,
            "work_state": "started",
            "active_generation": max(0, int(generation or 0)),
        },
        retention_seconds=retention_seconds,
    )
    redis.zadd(_heartbeat_key(service_type), {_digest(stable_id): now_epoch})


def _work_job_id(service_type: str, stable_id: str) -> str:
    if len(stable_id) <= 160 and _SAFE_RQ_JOB_ID.fullmatch(stable_id):
        return stable_id
    return f"aos_{service_type}_{_digest(stable_id)[:48]}"


def _callback_job_id(service_type: str, stable_id: str, generation: int) -> str:
    return f"{_work_job_id(service_type, stable_id)}_callback_g{max(0, int(generation or 0))}"


def _fetch_work_job(queue: Any, service_type: str, stable_id: str) -> Any | None:
    """Fetch current deterministic IDs and legacy raw IDs without enqueueing unsafe IDs."""
    normalized_id = _work_job_id(service_type, stable_id)
    job = queue.fetch_job(normalized_id)
    if job is not None or normalized_id == stable_id:
        return job
    try:
        return queue.fetch_job(stable_id)
    except Exception:
        return None


def _job_status_text(job: Any) -> str:
    if job is None:
        return "absent"
    raw = job.get_status(refresh=True)
    value = getattr(raw, "value", raw)
    return str(value or "").lower().split(".")[-1]


def _heartbeat_is_stale(record: dict[str, str], stale_after_seconds: int) -> bool:
    threshold = max(0, int(stale_after_seconds or 0))
    if threshold <= 0:
        return False
    try:
        heartbeat_epoch = int(
            record.get("heartbeat_epoch") or record.get("work_started_epoch") or 0
        )
    except (TypeError, ValueError):
        return False
    return heartbeat_epoch > 0 and (_epoch() - heartbeat_epoch) >= threshold


def _reset_stale_started_work(
    *,
    redis: Any,
    service_type: str,
    stable_id: str,
    record: dict[str, str],
    retention_seconds: int,
) -> int:
    """Archive and safely reset a video-style local job whose worker disappeared.

    This helper is only reached when callers explicitly enable stale replay and
    have already established that RQ no longer considers the job active and the
    durable heartbeat is stale. The work lock must be removed together with the
    stale result or the replacement RQ job would immediately dedupe itself.
    """
    replay_count = int(record.get("automatic_work_replay_count") or 0) + 1
    history = dict(record)
    history.update(
        {
            "archived_at": _now(),
            "automatic_work_replay_count": str(replay_count),
            "archive_reason": "stale_worker_heartbeat",
        }
    )
    history_key = _stale_history_key(service_type, stable_id, replay_count)
    pipe = redis.pipeline(transaction=True)
    pipe.hset(history_key, mapping=history)
    pipe.expire(history_key, max(3600, int(retention_seconds)))
    pipe.delete(_result_key(service_type, stable_id))
    pipe.delete(_work_lock_key(service_type, stable_id))
    pipe.zrem(_heartbeat_key(service_type), _digest(stable_id))
    pipe.zrem(_callback_pending_key(service_type), _digest(stable_id))
    pipe.execute()
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "service_type": service_type,
            "work_state": "stale_replay_authorized",
            "automatic_work_replay_count": replay_count,
            "automatic_work_replay_authorized_at": _now(),
            "callback_status": "not_ready",
            "phase": "accepted",
        },
        retention_seconds=retention_seconds,
    )
    _record_metric(redis, service_type, "stale_work_replay")
    return replay_count


def _persist_stale_worker_failure(
    *,
    redis: Any,
    callback_queue: Any,
    service_type: str,
    stable_id: str,
    payload: dict[str, Any],
    record: dict[str, str],
    callback_worker_method: str,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    result_ttl_seconds: int,
    failure_ttl_seconds: int,
) -> None:
    terminal_payload = {
        key: payload.get(key)
        for key in (
            "job_id",
            "idempotency_key",
            "dispatch_id",
            "dispatch_generation",
            "dispatch_token",
            "job_generation",
            "short_id",
        )
        if payload.get(key) is not None
    }
    terminal_payload.update(
        {
            "status": "failed",
            "error": "WORKER_LOST_AFTER_RETRIES",
        }
    )
    terminal_payload = _safe_payload(terminal_payload)
    result_json = _json(terminal_payload)
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "work_state": "work_failed",
            "terminal_result_type": "failed",
            "result_payload": result_json,
            "result_digest": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
            "error_category": "WORKER_LOST_AFTER_RETRIES",
            "work_error_classification": "worker_lost",
            "automatic_work_replay_count": int(record.get("automatic_work_replay_count") or 0),
            "work_completed_at": _now(),
            "work_completed_epoch": _epoch(),
            "callback_status": "pending",
            "callback_attempt_count": 0,
            "phase": "result_persisted",
            "heartbeat_at": _now(),
            "heartbeat_epoch": _epoch(),
        },
        retention_seconds=result_ttl_seconds,
    )
    redis.delete(_work_lock_key(service_type, stable_id))
    redis.zrem(_heartbeat_key(service_type), _digest(stable_id))
    _record_metric(redis, service_type, "stale_work_terminal_failure")
    _enqueue_callback(
        redis=redis,
        queue=callback_queue,
        service_type=service_type,
        stable_id=stable_id,
        generation=max(0, int(payload.get("dispatch_generation") or record.get("active_generation") or 0)),
        callback_worker_method=callback_worker_method,
        callback_timeout_seconds=callback_timeout_seconds,
        callback_max_attempts=callback_max_attempts,
        result_ttl_seconds=result_ttl_seconds,
        failure_ttl_seconds=failure_ttl_seconds,
    )


def _enqueue_callback(
    *,
    redis: Any,
    queue: Any,
    service_type: str,
    stable_id: str,
    generation: int,
    callback_worker_method: str,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    result_ttl_seconds: int,
    failure_ttl_seconds: int,
) -> tuple[Any | None, str]:
    record = load_result(redis, service_type, stable_id)
    if not record:
        raise DurableLifecycleError("Durable work result is unavailable.")
    if record.get("callback_status") == "complete":
        return None, "callback_already_completed"

    job_id = _callback_job_id(service_type, stable_id, generation)
    existing = queue.fetch_job(job_id)
    if existing is not None:
        status = _job_status_text(existing)
        if status in _ACTIVE_JOB_STATUSES:
            return existing, "callback_delivery_active"
        if status == "finished" and record.get("callback_status") == "complete":
            return existing, "callback_already_completed"
        existing.delete()

    callback_job = queue.enqueue(
        callback_worker_method,
        stable_id,
        job_id=job_id,
        job_timeout=max(30, min(int(callback_timeout_seconds or 120), 900)),
        result_ttl=max(3600, int(result_ttl_seconds)),
        failure_ttl=max(3600, int(failure_ttl_seconds)),
        retry=Retry(
            max=max(1, int(callback_max_attempts) - 1),
            interval=[15, 30, 60, 120, 300, 600, 900, 1800][: max(1, int(callback_max_attempts) - 1)],
        ),
    )
    now_epoch = _epoch()
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "callback_status": "pending",
            "callback_job_id": job_id,
            "next_callback_retry_at": now_epoch,
            "phase": "callback_pending",
        },
        retention_seconds=result_ttl_seconds,
    )
    redis.zadd(_callback_pending_key(service_type), {_digest(stable_id): now_epoch})
    _record_metric(redis, service_type, "callback_queued")
    return callback_job, "callback_replay_scheduled"


def _work_exception_category(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if any(token in name for token in ("timeout", "deadline")):
        return "WORK_TRANSPORT_TIMEOUT"
    if any(token in name for token in ("connection", "connect", "reset", "proxy", "unavailable")):
        return "WORK_TRANSPORT_UNCERTAIN"
    return "WORK_EXECUTION_FAILED"


def _work_error_kind(exc: Exception) -> str:
    if isinstance(exc, ProviderOutcomeUncertainError):
        return "provider_outcome_uncertain"
    if isinstance(exc, TerminalWorkError):
        return "terminal_work_error"
    if isinstance(exc, RetryableWorkError):
        return "retryable_work_error"
    name = exc.__class__.__name__.lower()
    if any(token in name for token in ("timeout", "connection", "connect", "reset", "temporar", "unavailable")):
        return "retryable_work_error"
    if isinstance(exc, (ValueError, TypeError, KeyError, AssertionError)):
        return "terminal_work_error"
    return "retryable_work_error"


def _rq_retry_evidence() -> tuple[int, int]:
    """Return current attempt number and remaining RQ retries without exposing job IDs."""
    job = get_current_job()
    if job is None:
        return 1, 0
    try:
        retries_left = max(0, int(getattr(job, "retries_left", 0) or 0))
    except Exception:
        retries_left = 0
    try:
        meta = dict(getattr(job, "meta", None) or {})
        attempt = max(1, int(meta.get("aos_work_attempt") or 0) + 1)
        meta["aos_work_attempt"] = attempt
        job.meta = meta
        job.save_meta()
    except Exception:
        attempt = 1
    return attempt, retries_left


def execute_work_job(
    *,
    redis: Any,
    queue: Any,
    service_type: str,
    payload: dict[str, Any],
    perform_work: Callable[[dict[str, Any]], dict[str, Any]],
    failure_payload: Callable[[dict[str, Any], str], dict[str, Any]],
    callback_worker_method: str,
    result_ttl_seconds: int,
    failure_ttl_seconds: int,
    work_lock_seconds: int,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    callback_queue: Any | None = None,
) -> dict[str, Any]:
    """Execute work with RQ retry semantics, then persist one terminal result.

    Retryable work errors are re-raised while RQ retries remain. Only a
    definitive error or retry exhaustion becomes a durable terminal failure.
    Callback delivery is always scheduled after terminal result persistence.
    """
    stable_id = _stable_id(payload)
    generation = max(0, int(payload.get("dispatch_generation") or 0))
    callback_target = callback_queue or queue
    existing = load_result(redis, service_type, stable_id)
    if existing.get("work_state") in _TERMINAL_WORK_STATES:
        _record_metric(redis, service_type, "side_effect_dedupe_hit")
        _enqueue_callback(
            redis=redis,
            queue=callback_target,
            service_type=service_type,
            stable_id=stable_id,
            generation=int(existing.get("active_generation") or generation),
            callback_worker_method=callback_worker_method,
            callback_timeout_seconds=callback_timeout_seconds,
            callback_max_attempts=callback_max_attempts,
            result_ttl_seconds=result_ttl_seconds,
            failure_ttl_seconds=failure_ttl_seconds,
        )
        return _parse_json(existing.get("result_payload"), {"status": existing.get("terminal_result_type") or "completed"})
    if existing.get("work_state") in {"started", "outcome_uncertain"}:
        _record_metric(redis, service_type, "duplicate_active")
        return {
            "job_id": payload.get("job_id"),
            "status": "work_outcome_uncertain" if existing.get("work_state") == "outcome_uncertain" else "work_active",
        }

    lock_key = _work_lock_key(service_type, stable_id)
    lock_value = f"g{generation}:{_epoch()}"
    if not redis.set(lock_key, lock_value, nx=True, ex=max(60, int(work_lock_seconds))):
        _record_metric(redis, service_type, "duplicate_active")
        return {"job_id": payload.get("job_id"), "status": "work_active"}

    attempt, retries_left = _rq_retry_evidence()
    max_attempts = max(attempt, attempt + retries_left)
    try:
        _write_record(
            redis,
            service_type,
            stable_id,
            {
                "service_type": service_type,
                "stable_job_id": _clean(payload.get("job_id"), limit=200),
                "stable_dispatch_id": _clean(payload.get("dispatch_id") or stable_id, limit=240),
                "active_generation": generation,
                "active_token": _clean(payload.get("dispatch_token"), limit=180),
                "callback_url": _clean(payload.get("callback_url"), limit=1000),
                "work_state": "started",
                "work_attempt_count": attempt,
                "work_max_attempts": max_attempts,
                "work_error_classification": "",
                "work_last_error_category": "",
                "work_started_at": _now(),
                "work_started_epoch": _epoch(),
                "heartbeat_at": _now(),
                "heartbeat_epoch": _epoch(),
                "phase": "starting",
                "callback_status": "not_ready",
                "callback_attempt_count": 0,
                "result_version": 2,
            },
            retention_seconds=result_ttl_seconds,
        )
        redis.zadd(_heartbeat_key(service_type), {_digest(stable_id): _epoch()})
        _record_metric(redis, service_type, "work_started")
        heartbeat_stop = threading.Event()

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(30):
                try:
                    heartbeat_work(
                        redis,
                        service_type=service_type,
                        stable_id=stable_id,
                        generation=generation,
                        phase="processing",
                        retention_seconds=result_ttl_seconds,
                    )
                except Exception:
                    continue

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"aos-heartbeat-{service_type}",
            daemon=True,
        )
        heartbeat_thread.start()
        terminal_payload: dict[str, Any]
        work_state: str
        error_category = ""
        error_classification = ""
        try:
            try:
                terminal_payload = perform_work(payload)
                terminal_status = _clean(terminal_payload.get("status"), limit=80).lower()
                work_state = "work_failed" if terminal_status == "failed" else "work_complete"
                error_category = _clean(terminal_payload.get("error"), limit=120)
                error_classification = "terminal_work_error" if work_state == "work_failed" else ""
            except Exception as exc:
                exception_category = _work_exception_category(exc)
                classification = _work_error_kind(exc)
                candidate = failure_payload(payload, exception_category)
                candidate_status = _clean(candidate.get("status"), limit=80).lower()
                if classification == "provider_outcome_uncertain" or candidate_status in {
                    "outcome_uncertain",
                    "delivery_uncertain",
                }:
                    terminal_payload = candidate
                    work_state = "outcome_uncertain"
                    error_category = _clean(candidate.get("error"), limit=120) or exception_category
                    error_classification = "provider_outcome_uncertain"
                elif classification == "retryable_work_error" and retries_left > 0:
                    next_retry = _epoch() + min(1800, 15 * (2 ** min(attempt - 1, 7)))
                    _write_record(
                        redis,
                        service_type,
                        stable_id,
                        {
                            "work_state": "retrying",
                            "work_attempt_count": attempt,
                            "work_max_attempts": max_attempts,
                            "work_error_classification": "retryable_work_error",
                            "work_last_error_category": exception_category,
                            "next_work_retry_at": next_retry,
                            "phase": "work_retrying",
                            "heartbeat_at": _now(),
                            "heartbeat_epoch": _epoch(),
                        },
                        retention_seconds=result_ttl_seconds,
                    )
                    redis.zrem(_heartbeat_key(service_type), _digest(stable_id))
                    _record_metric(redis, service_type, "work_retryable_error")
                    raise
                else:
                    terminal_payload = candidate
                    work_state = "work_failed"
                    error_category = _clean(candidate.get("error"), limit=120) or exception_category
                    error_classification = (
                        "retry_exhausted" if classification == "retryable_work_error" else "terminal_work_error"
                    )
                    if classification == "retryable_work_error":
                        _record_metric(redis, service_type, "work_retry_exhausted")
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)

        terminal_payload = _safe_payload(terminal_payload)
        completed_epoch = _epoch()
        result_json = _json(terminal_payload)
        terminal_payload = _parse_json(result_json, terminal_payload)
        _write_record(
            redis,
            service_type,
            stable_id,
            {
                "work_state": work_state,
                "terminal_result_type": _clean(terminal_payload.get("status"), limit=80),
                "result_payload": result_json,
                "result_digest": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
                "error_category": error_category,
                "work_error_classification": error_classification,
                "work_attempt_count": attempt,
                "work_max_attempts": max_attempts,
                "work_retry_exhausted_at": _now() if error_classification == "retry_exhausted" else "",
                "work_completed_at": _now(),
                "work_completed_epoch": completed_epoch,
                "callback_status": "not_ready" if work_state == "outcome_uncertain" else "pending",
                "callback_attempt_count": 0,
                "next_callback_retry_at": completed_epoch,
                "last_callback_error_category": "",
                "callback_completed_at": "",
                "phase": "outcome_uncertain" if work_state == "outcome_uncertain" else "result_persisted",
                "heartbeat_at": _now(),
                "heartbeat_epoch": completed_epoch,
            },
            retention_seconds=result_ttl_seconds,
        )
        redis.zrem(_heartbeat_key(service_type), _digest(stable_id))
        _record_metric(redis, service_type, "result_persisted")
        if work_state == "outcome_uncertain":
            redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
            _record_metric(redis, service_type, "dispatch_uncertain")
            return terminal_payload
        if work_state == "work_failed":
            _record_metric(redis, service_type, "work_failed")
            _record_metric(redis, service_type, "terminal_work_failure")
        else:
            _record_metric(redis, service_type, "work_completed")
        try:
            _enqueue_callback(
                redis=redis,
                queue=callback_target,
                service_type=service_type,
                stable_id=stable_id,
                generation=generation,
                callback_worker_method=callback_worker_method,
                callback_timeout_seconds=callback_timeout_seconds,
                callback_max_attempts=callback_max_attempts,
                result_ttl_seconds=result_ttl_seconds,
                failure_ttl_seconds=failure_ttl_seconds,
            )
        except Exception:
            _write_record(
                redis,
                service_type,
                stable_id,
                {
                    "callback_status": "pending",
                    "last_callback_error_category": "CALLBACK_ENQUEUE_FAILED",
                    "next_callback_retry_at": _epoch() + 30,
                },
                retention_seconds=result_ttl_seconds,
            )
        return terminal_payload
    finally:
        current = redis.get(lock_key)
        current_text = current.decode("utf-8", "replace") if isinstance(current, bytes) else str(current or "")
        if current_text == lock_value:
            redis.delete(lock_key)

def _response_error_code(response: Any) -> str:
    try:
        data = response.json()
    except Exception:
        return ""
    if isinstance(data, dict):
        for key in ("error", "error_code", "code"):
            value = data.get(key)
            if value:
                return _clean(value, limit=120).upper()
        message = data.get("message")
        if isinstance(message, dict):
            for key in ("error", "error_code", "code"):
                value = message.get(key)
                if value:
                    return _clean(value, limit=120).upper()
    return ""


def deliver_callback(
    *,
    redis: Any,
    service_type: str,
    stable_id: str,
    send_callback: Callable[[str, dict[str, Any]], Any],
    result_ttl_seconds: int,
    callback_max_attempts: int,
) -> dict[str, Any]:
    """Deliver one persisted callback without invoking the external work function."""
    stable_id = _clean(stable_id, limit=200)
    record = load_result(redis, service_type, stable_id)
    if not record or record.get("work_state") not in _TERMINAL_WORK_STATES:
        raise DurableLifecycleError("Durable terminal result is unavailable.")
    if record.get("callback_status") == "complete":
        return {"ok": True, "callback_status": "complete", "idempotent": True}
    if record.get("callback_status") == "superseded":
        return {"ok": True, "callback_status": "superseded"}

    lock_key = _callback_lock_key(service_type, stable_id)
    lock_value = f"{_epoch()}"
    if not redis.set(lock_key, lock_value, nx=True, ex=120):
        return {"ok": True, "callback_status": "duplicate_active"}

    try:
        record = load_result(redis, service_type, stable_id)
        attempt = int(record.get("callback_attempt_count") or 0) + 1
        result_payload = _parse_json(record.get("result_payload"), None)
        if not isinstance(result_payload, dict):
            _write_record(
                redis,
                service_type,
                stable_id,
                {
                    "callback_status": "dead_letter",
                    "last_callback_error_category": "CALLBACK_RESULT_INVALID",
                    "callback_completed_at": _now(),
                    "next_callback_retry_at": "",
                },
                retention_seconds=result_ttl_seconds,
            )
            redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
            _record_metric(redis, service_type, "callback_dead_lettered")
            return {
                "ok": False,
                "callback_status": "dead_letter",
                "error": "CALLBACK_RESULT_INVALID",
            }

        # The stable job ID is stored separately from the result JSON. Restore it
        # defensively for records written by older workers, but never invent
        # domain output metadata such as duration or object keys.
        result_payload["job_id"] = (
            _clean(result_payload.get("job_id"), limit=200)
            or record.get("stable_job_id")
            or stable_id
        )
        if not result_payload.get("status"):
            result_payload["status"] = record.get("terminal_result_type") or ""
        result_payload.update(
            {
                "idempotency_key": stable_id,
                "dispatch_id": record.get("stable_dispatch_id") or stable_id,
                "dispatch_generation": int(record.get("active_generation") or 0),
                "dispatch_token": record.get("active_token") or "",
            }
        )
        _write_record(
            redis,
            service_type,
            stable_id,
            {
                "callback_status": "retrying" if attempt > 1 else "pending",
                "callback_attempt_count": attempt,
                "last_callback_attempt_at": _now(),
                "next_callback_retry_at": "",
            },
            retention_seconds=result_ttl_seconds,
        )
        if attempt > 1:
            _record_metric(redis, service_type, "callback_retried")

        try:
            response = send_callback(record.get("callback_url") or "", result_payload)
            status_code = int(getattr(response, "status_code", 200) or 200)
            if 200 <= status_code < 300:
                completed_epoch = _epoch()
                _write_record(
                    redis,
                    service_type,
                    stable_id,
                    {
                        "callback_status": "complete",
                        "callback_completed_at": _now(),
                        "callback_completed_epoch": completed_epoch,
                        "last_callback_error_category": "",
                        "next_callback_retry_at": "",
                        "phase": "callback_complete",
                    },
                    retention_seconds=result_ttl_seconds,
                )
                redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
                _record_metric(redis, service_type, "callback_completed")
                return {"ok": True, "callback_status": "complete", "attempt": attempt}

            error_code = _response_error_code(response)
            if error_code in _RETRYABLE_CALLBACK_CODES:
                raise CallbackDeliveryRetryableError(error_code)
            if error_code in _TERMINAL_SUPERSEDED_CODES:
                _write_record(
                    redis,
                    service_type,
                    stable_id,
                    {
                        "callback_status": "superseded",
                        "last_callback_error_category": error_code,
                        "callback_completed_at": _now(),
                        "next_callback_retry_at": "",
                    },
                    retention_seconds=result_ttl_seconds,
                )
                redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
                _record_metric(redis, service_type, "callback_superseded")
                return {"ok": True, "callback_status": "superseded", "error": error_code}
            if error_code in _TOKEN_MISMATCH_CODES or status_code in {401, 403}:
                category = error_code or "CALLBACK_AUTH_REJECTED"
                _write_record(
                    redis,
                    service_type,
                    stable_id,
                    {
                        "callback_status": "dead_letter",
                        "last_callback_error_category": category,
                        "callback_completed_at": _now(),
                        "next_callback_retry_at": "",
                    },
                    retention_seconds=result_ttl_seconds,
                )
                redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
                _record_metric(redis, service_type, "callback_token_mismatch")
                _record_metric(redis, service_type, "callback_dead_lettered")
                return {"ok": False, "callback_status": "dead_letter", "error": category}
            if status_code not in _RETRYABLE_HTTP:
                category = error_code or "CALLBACK_NON_RETRYABLE_REJECTION"
                _write_record(
                    redis,
                    service_type,
                    stable_id,
                    {
                        "callback_status": "dead_letter",
                        "last_callback_error_category": category,
                        "callback_completed_at": _now(),
                        "next_callback_retry_at": "",
                    },
                    retention_seconds=result_ttl_seconds,
                )
                redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
                _record_metric(redis, service_type, "callback_dead_lettered")
                return {"ok": False, "callback_status": "dead_letter", "error": category}
            logger.warning(
                "Companion callback temporarily rejected service=%s status=%s category=%s",
                service_type,
                status_code,
                error_code or "none",
            )
            raise CallbackDeliveryRetryableError("CALLBACK_TEMPORARY_HTTP_FAILURE")
        except CallbackDeliveryRetryableError:
            raise
        except Exception as exc:
            name = exc.__class__.__name__.lower()
            category = "CALLBACK_TIMEOUT" if "timeout" in name else "CALLBACK_TRANSPORT_FAILURE"
            raise CallbackDeliveryRetryableError(category) from exc
    except CallbackDeliveryRetryableError as exc:
        record = load_result(redis, service_type, stable_id)
        attempt = int(record.get("callback_attempt_count") or 1)
        category = _clean(exc, limit=120) or "CALLBACK_DELIVERY_FAILED"
        if attempt >= max(1, int(callback_max_attempts)):
            _write_record(
                redis,
                service_type,
                stable_id,
                {
                    "callback_status": "dead_letter",
                    "last_callback_error_category": category,
                    "callback_completed_at": _now(),
                    "next_callback_retry_at": "",
                },
                retention_seconds=result_ttl_seconds,
            )
            redis.zrem(_callback_pending_key(service_type), _digest(stable_id))
            _record_metric(redis, service_type, "callback_dead_lettered")
            return {"ok": False, "callback_status": "dead_letter", "error": category}
        delay = min(1800, 15 * (2 ** min(attempt - 1, 7)))
        _write_record(
            redis,
            service_type,
            stable_id,
            {
                "callback_status": "retrying",
                "last_callback_error_category": category,
                "next_callback_retry_at": _epoch() + delay,
            },
            retention_seconds=result_ttl_seconds,
        )
        raise
    finally:
        current = redis.get(lock_key)
        current_text = current.decode("utf-8", "replace") if isinstance(current, bytes) else str(current or "")
        if current_text == lock_value:
            redis.delete(lock_key)


def enqueue_or_reconcile(
    *,
    redis: Any,
    queue: Any,
    worker_method: str,
    callback_worker_method: str,
    service_type: str,
    payload: dict[str, Any],
    job_timeout: int,
    result_ttl: int,
    failure_ttl: int,
    retry: Retry,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    durable_result_ttl_seconds: int | None = None,
    callback_queue: Any | None = None,
    stale_heartbeat_seconds: int = 0,
    max_stale_work_replays: int = 0,
    allow_stale_work_replay: bool = False,
) -> DispatchDecision:
    """Idempotently enqueue work or reconcile its durable result/callback state."""
    stable_id = _stable_id(payload)
    durable_ttl = max(int(result_ttl), int(durable_result_ttl_seconds or result_ttl))
    requested_generation = max(0, int(payload.get("dispatch_generation") or 0))
    record = load_result(redis, service_type, stable_id)
    existing = _fetch_work_job(queue, service_type, stable_id)
    callback_target = callback_queue or queue
    enqueue_outcome = "enqueued"

    if record.get("work_state") in _TERMINAL_WORK_STATES:
        authoritative = int(record.get("active_generation") or 0)
        terminal_type = record.get("terminal_result_type", "")
        result_digest = record.get("result_digest", "")
        if requested_generation < authoritative:
            return DispatchDecision(
                existing,
                "newer_generation_exists",
                authoritative,
                record["work_state"],
                record.get("callback_status", ""),
                terminal_type,
                result_digest,
            )
        if requested_generation > authoritative:
            # Frappe may have lost the callback response or have incomplete
            # terminal state even though the companion marked delivery complete.
            # A signed newer generation explicitly re-arms callback delivery from
            # the persisted result; external work is never re-executed.
            _write_record(
                redis,
                service_type,
                stable_id,
                {
                    "active_generation": requested_generation,
                    "active_token": _clean(payload.get("dispatch_token"), limit=180),
                    "stable_dispatch_id": _clean(payload.get("dispatch_id") or stable_id, limit=240),
                    "callback_url": _clean(payload.get("callback_url"), limit=1000),
                    "callback_status": "pending",
                    "callback_attempt_count": 0,
                    "last_callback_error_category": "",
                    "callback_completed_at": "",
                },
                retention_seconds=durable_ttl,
            )
            authoritative = requested_generation
        elif record.get("callback_status") == "complete":
            return DispatchDecision(
                existing,
                "callback_already_completed",
                authoritative,
                record["work_state"],
                "complete",
                terminal_type,
                result_digest,
            )
        _enqueue_callback(
            redis=redis,
            queue=callback_target,
            service_type=service_type,
            stable_id=stable_id,
            generation=authoritative,
            callback_worker_method=callback_worker_method,
            callback_timeout_seconds=callback_timeout_seconds,
            callback_max_attempts=callback_max_attempts,
            result_ttl_seconds=durable_ttl,
            failure_ttl_seconds=failure_ttl,
        )
        _record_metric(redis, service_type, "work_result_replay")
        outcome = "callback_replay_scheduled" if requested_generation >= authoritative else "work_complete_callback_pending"
        return DispatchDecision(
            existing,
            outcome,
            authoritative,
            record["work_state"],
            "pending",
            terminal_type,
            result_digest,
        )

    if existing is not None:
        status = _job_status_text(existing)
        if status in _ACTIVE_JOB_STATUSES:
            args = list(getattr(existing, "args", None) or [])
            existing_payload = args[0] if args and isinstance(args[0], dict) else {}
            authoritative = max(0, int(existing_payload.get("dispatch_generation") or record.get("active_generation") or 0))
            if requested_generation < authoritative:
                return DispatchDecision(
                    existing,
                    "newer_generation_exists",
                    authoritative,
                    record.get("work_state", "started"),
                    record.get("callback_status", "not_ready"),
                    record.get("terminal_result_type", ""),
                    record.get("result_digest", ""),
                )
            if status == "started" or requested_generation == authoritative:
                _record_metric(redis, service_type, "duplicate_active")
                return DispatchDecision(
                    existing,
                    "duplicate_active",
                    authoritative,
                    record.get("work_state", "started"),
                    record.get("callback_status", "not_ready"),
                )
            # A queued/deferred/scheduled older generation has not started external
            # work and can be replaced safely. A started generation is never invalidated.
            existing.delete()
            existing = None
            enqueue_outcome = "stale_generation_replaced"
        elif status == "finished" and record.get("work_state") not in _TERMINAL_WORK_STATES:
            _record_metric(redis, service_type, "reconciliation_attempt")
            return DispatchDecision(
                existing,
                "reconciliation_pending",
                int(record.get("active_generation") or 0),
                record.get("work_state", "unknown"),
                record.get("callback_status", "not_ready"),
                record.get("terminal_result_type", ""),
                record.get("result_digest", ""),
            )
        elif status in {"failed", "stopped", "canceled", "cancelled"} and record.get("work_state") in {"started", "outcome_uncertain"}:
            if (
                allow_stale_work_replay
                and record.get("work_state") == "started"
                and _heartbeat_is_stale(record, stale_heartbeat_seconds)
            ):
                replay_count = int(record.get("automatic_work_replay_count") or 0)
                if replay_count < max(0, int(max_stale_work_replays or 0)):
                    existing.delete()
                    existing = None
                    _reset_stale_started_work(
                        redis=redis,
                        service_type=service_type,
                        stable_id=stable_id,
                        record=record,
                        retention_seconds=durable_ttl,
                    )
                    record = load_result(redis, service_type, stable_id)
                    enqueue_outcome = "stale_work_replayed"
                else:
                    _persist_stale_worker_failure(
                        redis=redis,
                        callback_queue=callback_target,
                        service_type=service_type,
                        stable_id=stable_id,
                        payload=payload,
                        record=record,
                        callback_worker_method=callback_worker_method,
                        callback_timeout_seconds=callback_timeout_seconds,
                        callback_max_attempts=callback_max_attempts,
                        result_ttl_seconds=durable_ttl,
                        failure_ttl_seconds=failure_ttl,
                    )
                    terminal = load_result(redis, service_type, stable_id)
                    return DispatchDecision(
                        existing,
                        "callback_replay_scheduled",
                        int(terminal.get("active_generation") or requested_generation),
                        "work_failed",
                        "pending",
                        terminal.get("terminal_result_type", "failed"),
                        terminal.get("result_digest", ""),
                    )
            else:
                _record_metric(redis, service_type, "reconciliation_attempt")
                return DispatchDecision(
                    existing,
                    "reconciliation_pending",
                    int(record.get("active_generation") or 0),
                    "outcome_uncertain",
                    record.get("callback_status", "not_ready"),
                    record.get("terminal_result_type", ""),
                    record.get("result_digest", ""),
                )
        elif existing is not None:
            existing.delete()

    if record.get("work_state") in {"started", "outcome_uncertain"}:
        if (
            allow_stale_work_replay
            and record.get("work_state") == "started"
            and _heartbeat_is_stale(record, stale_heartbeat_seconds)
        ):
            replay_count = int(record.get("automatic_work_replay_count") or 0)
            if replay_count < max(0, int(max_stale_work_replays or 0)):
                _reset_stale_started_work(
                    redis=redis,
                    service_type=service_type,
                    stable_id=stable_id,
                    record=record,
                    retention_seconds=durable_ttl,
                )
                record = load_result(redis, service_type, stable_id)
                enqueue_outcome = "stale_work_replayed"
            else:
                _persist_stale_worker_failure(
                    redis=redis,
                    callback_queue=callback_target,
                    service_type=service_type,
                    stable_id=stable_id,
                    payload=payload,
                    record=record,
                    callback_worker_method=callback_worker_method,
                    callback_timeout_seconds=callback_timeout_seconds,
                    callback_max_attempts=callback_max_attempts,
                    result_ttl_seconds=durable_ttl,
                    failure_ttl_seconds=failure_ttl,
                )
                terminal = load_result(redis, service_type, stable_id)
                return DispatchDecision(
                    None,
                    "callback_replay_scheduled",
                    int(terminal.get("active_generation") or requested_generation),
                    "work_failed",
                    "pending",
                    terminal.get("terminal_result_type", "failed"),
                    terminal.get("result_digest", ""),
                )
        else:
            _record_metric(redis, service_type, "reconciliation_attempt")
            return DispatchDecision(
                None,
                "reconciliation_pending",
                int(record.get("active_generation") or 0),
                record.get("work_state", "outcome_uncertain"),
                record.get("callback_status", "not_ready"),
                record.get("terminal_result_type", ""),
                record.get("result_digest", ""),
            )

    job = queue.enqueue(
        worker_method,
        payload,
        job_id=_work_job_id(service_type, stable_id),
        job_timeout=job_timeout,
        result_ttl=result_ttl,
        failure_ttl=failure_ttl,
        retry=retry,
    )
    _record_metric(redis, service_type, "work_queued")
    return DispatchDecision(job, enqueue_outcome, requested_generation, "queued", "not_ready")


def job_status(
    redis: Any,
    service_type: str,
    stable_id: str,
    queue: Any,
    *,
    stale_heartbeat_seconds: int = 0,
) -> dict[str, Any]:
    """Return bounded private reconciliation state without result content or tokens."""
    stable_id = _clean(stable_id, limit=200)
    record = load_result(redis, service_type, stable_id)
    job = _fetch_work_job(queue, service_type, stable_id)
    rq_status = _job_status_text(job)
    if record.get("work_state") in _TERMINAL_WORK_STATES:
        state = "callback_complete" if record.get("callback_status") == "complete" else "callback_pending"
        if record.get("callback_status") == "dead_letter":
            state = "failed"
        elif record.get("work_state") == "work_failed" and record.get("callback_status") == "complete":
            state = "failed"
    elif rq_status in _ACTIVE_JOB_STATUSES:
        state = "started" if rq_status == "started" else "queued"
    elif record.get("work_state") in {"started", "outcome_uncertain"}:
        if (
            record.get("work_state") == "started"
            and rq_status not in _ACTIVE_JOB_STATUSES
            and _heartbeat_is_stale(record, stale_heartbeat_seconds)
        ):
            state = "failed"
        else:
            state = "started" if record.get("work_state") == "started" else "unknown"
    elif rq_status in {"failed", "stopped", "canceled", "cancelled"}:
        state = "failed"
    else:
        state = "absent"
    return {
        "ok": True,
        "state": state,
        "rq_state": rq_status,
        "work_state": record.get("work_state") or "absent",
        "callback_state": record.get("callback_status") or "not_ready",
        "dispatch_generation": int(record.get("active_generation") or 0),
        "heartbeat_at": record.get("heartbeat_at") or None,
        "phase": record.get("phase") if record.get("phase") in _ALLOWED_PHASES else None,
        "callback_attempt_count": int(record.get("callback_attempt_count") or 0),
        "terminal_result_type": record.get("terminal_result_type") or None,
        "result_digest": record.get("result_digest") or None,
        "work_error_classification": record.get("work_error_classification") or None,
        "work_attempt_count": int(record.get("work_attempt_count") or 0),
        "work_max_attempts": int(record.get("work_max_attempts") or 0),
    }


def resolve_uncertain_outcome(
    *,
    redis: Any,
    queue: Any,
    service_type: str,
    stable_id: str,
    resolution: str,
    callback_worker_method: str,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    result_ttl_seconds: int,
    failure_ttl_seconds: int,
    terminal_payload: dict[str, Any] | None = None,
    operator_reference: str | None = None,
    max_manual_resends: int = 3,
    callback_queue: Any | None = None,
) -> dict[str, Any]:
    """Resolve a provider-uncertain result without blind automatic resend."""
    stable_id = _clean(stable_id, limit=200)
    decision = _clean(resolution, limit=80).lower()
    allowed = {"confirmed_accepted", "confirmed_failed", "approved_resend", "permanently_unresolved"}
    if decision not in allowed:
        raise DurableLifecycleError("Unsupported uncertainty resolution.")
    record = load_result(redis, service_type, stable_id)
    if record.get("work_state") != "outcome_uncertain":
        raise DurableLifecycleError("The durable work result is not awaiting uncertainty resolution.")
    history_count = int(record.get("uncertainty_resolution_count") or 0) + 1
    operator_digest = hashlib.sha256(
        _clean(operator_reference or "signed-internal-operator", limit=200).encode("utf-8")
    ).hexdigest()
    if decision == "approved_resend":
        resend_count = int(record.get("operator_resend_count") or 0) + 1
        if resend_count > max(1, int(max_manual_resends)):
            raise DurableLifecycleError("Maximum approved resend count exceeded.")
        job = _fetch_work_job(queue, service_type, stable_id)
        if job is not None and _job_status_text(job) == "started":
            raise DurableLifecycleError("Cannot approve resend while the original work is still running.")
        if job is not None:
            job.delete()
        _write_record(
            redis,
            service_type,
            stable_id,
            {
                "work_state": "manual_resend_approved",
                "phase": "manual_review",
                "operator_resend_count": resend_count,
                "uncertainty_resolution_count": history_count,
                "uncertainty_resolution": decision,
                "uncertainty_resolved_at": _now(),
                "uncertainty_operator_digest": operator_digest,
                "callback_status": "not_ready",
                "result_payload": "",
                "terminal_result_type": "",
                "result_digest": "",
            },
            retention_seconds=result_ttl_seconds,
        )
        _record_metric(redis, service_type, "uncertainty_resolved")
        return {"ok": True, "resolution": decision, "work_state": "manual_resend_approved"}

    payload = dict(terminal_payload or _parse_json(record.get("result_payload"), {}))
    if decision == "confirmed_accepted":
        payload.update({"status": "delivered", "error": None, "provider_resolution": decision})
        work_state = "work_complete"
    else:
        payload.update(
            {
                "status": "failed",
                "error": "NOTIFICATION_PROVIDER_CONFIRMED_FAILED"
                if decision == "confirmed_failed"
                else "NOTIFICATION_PROVIDER_PERMANENTLY_UNRESOLVED",
                "provider_resolution": decision,
            }
        )
        work_state = "work_failed"
    result_json = _json(payload)
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "work_state": work_state,
            "terminal_result_type": payload["status"],
            "result_payload": result_json,
            "result_digest": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
            "callback_status": "pending",
            "callback_attempt_count": 0,
            "uncertainty_resolution_count": history_count,
            "uncertainty_resolution": decision,
            "uncertainty_resolved_at": _now(),
            "uncertainty_operator_digest": operator_digest,
            "phase": "result_persisted",
        },
        retention_seconds=result_ttl_seconds,
    )
    _enqueue_callback(
        redis=redis,
        queue=callback_queue or queue,
        service_type=service_type,
        stable_id=stable_id,
        generation=int(record.get("active_generation") or 0),
        callback_worker_method=callback_worker_method,
        callback_timeout_seconds=callback_timeout_seconds,
        callback_max_attempts=callback_max_attempts,
        result_ttl_seconds=result_ttl_seconds,
        failure_ttl_seconds=failure_ttl_seconds,
    )
    _record_metric(redis, service_type, "uncertainty_resolved")
    return {"ok": True, "resolution": decision, "work_state": work_state}


def authorize_work_replay(
    *,
    redis: Any,
    queue: Any,
    service_type: str,
    stable_id: str,
    result_ttl_seconds: int,
    max_operator_replays: int = 3,
    callback_queue: Any | None = None,
) -> dict[str, Any]:
    """Explicitly archive and reset one terminal work result for operator replay."""
    stable_id = _clean(stable_id, limit=200)
    record = load_result(redis, service_type, stable_id)
    if record.get("work_state") not in _TERMINAL_WORK_STATES:
        raise DurableLifecycleError("Only a terminal work result can be replayed.")
    replay_count = int(record.get("operator_work_replay_count") or 0) + 1
    if replay_count > max(1, int(max_operator_replays)):
        raise DurableLifecycleError("Maximum operator work replay count exceeded.")
    work_job = _fetch_work_job(queue, service_type, stable_id)
    if work_job is not None and _job_status_text(work_job) == "started":
        raise DurableLifecycleError("Cannot replay work while the previous execution is still running.")
    callback_job = None
    callback_target = callback_queue or queue
    callback_job_id = _clean(record.get("callback_job_id"), limit=240)
    if callback_job_id:
        callback_job = callback_target.fetch_job(callback_job_id)
        if callback_job is not None and _job_status_text(callback_job) == "started":
            raise DurableLifecycleError("Cannot replay work while callback delivery is still running.")

    history_key = _history_key(service_type, stable_id, replay_count)
    history = dict(record)
    history.update({"archived_at": _now(), "operator_work_replay_count": str(replay_count)})
    pipe = redis.pipeline(transaction=True)
    pipe.hset(history_key, mapping=history)
    pipe.expire(history_key, max(3600, int(result_ttl_seconds)))
    pipe.delete(_result_key(service_type, stable_id))
    pipe.zrem(_callback_pending_key(service_type), _digest(stable_id))
    pipe.zrem(_heartbeat_key(service_type), _digest(stable_id))
    pipe.execute()
    if work_job is not None:
        work_job.delete()
    if callback_job is not None:
        callback_job.delete()
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "service_type": service_type,
            "work_state": "work_replay_authorized",
            "operator_work_replay_count": replay_count,
            "operator_work_replay_authorized_at": _now(),
            "callback_status": "not_ready",
            "phase": "accepted",
        },
        retention_seconds=result_ttl_seconds,
    )
    return {"ok": True, "outcome": "work_replay_authorized", "replay_count": replay_count}


def replay_callback_delivery(
    *,
    redis: Any,
    queue: Any,
    service_type: str,
    stable_id: str,
    callback_worker_method: str,
    callback_timeout_seconds: int,
    callback_max_attempts: int,
    result_ttl_seconds: int,
    failure_ttl_seconds: int,
    callback_queue: Any | None = None,
) -> dict[str, Any]:
    record = load_result(redis, service_type, stable_id)
    if record.get("work_state") not in _TERMINAL_WORK_STATES:
        raise DurableLifecycleError("No terminal work result is available for callback replay.")
    generation = int(record.get("active_generation") or 0)
    _write_record(
        redis,
        service_type,
        stable_id,
        {
            "callback_status": "pending",
            "callback_attempt_count": 0,
            "last_callback_error_category": "",
            "callback_completed_at": "",
        },
        retention_seconds=result_ttl_seconds,
    )
    _, outcome = _enqueue_callback(
        redis=redis,
        queue=callback_queue or queue,
        service_type=service_type,
        stable_id=stable_id,
        generation=generation,
        callback_worker_method=callback_worker_method,
        callback_timeout_seconds=callback_timeout_seconds,
        callback_max_attempts=callback_max_attempts,
        result_ttl_seconds=result_ttl_seconds,
        failure_ttl_seconds=failure_ttl_seconds,
    )
    return {"ok": True, "outcome": outcome, "dispatch_generation": generation}


def render_lifecycle_metrics(redis: Any, service_type: str) -> str:
    metrics = _decode_map(redis.hgetall(_metrics_key(service_type)) or {})
    names = {
        "work_queued": "aos_companion_work_jobs_queued_total",
        "work_started": "aos_companion_work_jobs_started_total",
        "work_completed": "aos_companion_work_jobs_completed_total",
        "work_failed": "aos_companion_work_jobs_failed_total",
        "result_persisted": "aos_companion_durable_results_persisted_total",
        "callback_queued": "aos_companion_callback_jobs_queued_total",
        "callback_retried": "aos_companion_callback_jobs_retried_total",
        "callback_completed": "aos_companion_callback_jobs_completed_total",
        "callback_dead_lettered": "aos_companion_callback_jobs_dead_lettered_total",
        "dispatch_uncertain": "aos_companion_dispatch_uncertain_total",
        "reconciliation_attempt": "aos_companion_reconciliation_attempts_total",
        "duplicate_active": "aos_companion_duplicate_active_observations_total",
        "work_result_replay": "aos_companion_work_result_replays_total",
        "side_effect_dedupe_hit": "aos_companion_side_effect_dedupe_hits_total",
        "callback_token_mismatch": "aos_companion_callback_token_mismatches_total",
        "callback_superseded": "aos_companion_callback_superseded_total",
        "work_retryable_error": "aos_companion_work_retryable_errors_total",
        "work_retry_exhausted": "aos_companion_work_retry_exhausted_total",
        "terminal_work_failure": "aos_companion_terminal_work_failures_total",
        "uncertainty_resolved": "aos_companion_uncertainty_resolutions_total",
        "manual_review": "aos_companion_manual_review_total",
        "durable_ttl_repaired": "aos_companion_durable_ttl_repairs_total",
        "stale_index_cleaned": "aos_companion_stale_index_cleanup_total",
        "stale_work_replay": "aos_companion_stale_work_replays_total",
        "stale_work_terminal_failure": "aos_companion_stale_work_terminal_failures_total",
    }
    lines: list[str] = [f'aos_companion_lifecycle_metrics_ready{{service_type="{service_type}"}} 1']
    for event, metric_name in names.items():
        value = int(metrics.get(event) or 0)
        lines.append(f'{metric_name}{{service_type="{service_type}"}} {value}')
    now_epoch = _epoch()
    pending_key = _callback_pending_key(service_type)
    for raw_member in redis.zrange(pending_key, 0, -1):
        member = raw_member.decode("utf-8", "replace") if isinstance(raw_member, bytes) else str(raw_member)
        if not redis.exists(f"aos:worker-result:{service_type}:{member}"):
            redis.zrem(pending_key, member)
            _record_metric(redis, service_type, "stale_index_cleaned")
    pending = int(redis.zcard(pending_key) or 0)
    oldest = redis.zrange(pending_key, 0, 0, withscores=True)
    oldest_age = max(0, now_epoch - int(oldest[0][1])) if oldest else 0
    heartbeat_key = _heartbeat_key(service_type)
    for raw_member in redis.zrange(heartbeat_key, 0, -1):
        member = raw_member.decode("utf-8", "replace") if isinstance(raw_member, bytes) else str(raw_member)
        if not redis.exists(f"aos:worker-result:{service_type}:{member}"):
            redis.zrem(heartbeat_key, member)
            _record_metric(redis, service_type, "stale_index_cleaned")
    stale_before = now_epoch - 300
    stale_heartbeats = int(redis.zcount(heartbeat_key, 0, stale_before) or 0)
    lines.extend(
        [
            f'aos_companion_callback_pending{{service_type="{service_type}"}} {pending}',
            f'aos_companion_callback_oldest_age_seconds{{service_type="{service_type}"}} {oldest_age}',
            f'aos_companion_active_heartbeat_stale{{service_type="{service_type}"}} {stale_heartbeats}',
        ]
    )
    return "\n".join(lines) + "\n"
