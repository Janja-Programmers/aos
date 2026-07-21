"""Idempotent work dispatch backed by durable Redis work results."""

from __future__ import annotations

from typing import Any

from rq import Retry

from app.config import get_settings
from app.durable_lifecycle import enqueue_or_reconcile
from app.queue import get_redis

_SERVICE_TYPE = "video_processing"
_CALLBACK_WORKER = "app.worker.deliver_callback_job"


def enqueue_idempotent(
    *,
    queue: Any,
    worker_method: str,
    payload: dict[str, Any],
    job_timeout: int,
    result_ttl: int,
    failure_ttl: int,
    retry: Retry,
) -> tuple[Any, str]:
    settings = get_settings()
    decision = enqueue_or_reconcile(
        redis=get_redis(),
        queue=queue,
        worker_method=worker_method or "app.worker.process_video_job",
        callback_worker_method=_CALLBACK_WORKER,
        service_type=_SERVICE_TYPE,
        payload=payload,
        job_timeout=job_timeout,
        result_ttl=result_ttl,
        failure_ttl=failure_ttl,
        retry=retry,
        callback_timeout_seconds=int(getattr(settings, "callback_job_timeout_seconds", 120)),
        callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
        durable_result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", result_ttl)),
    )
    return decision.job, decision.outcome


def dispatch_details(payload: dict[str, Any], queue: Any) -> dict[str, Any]:
    settings = get_settings()
    decision = enqueue_or_reconcile(
        redis=get_redis(),
        queue=queue,
        worker_method="app.worker.process_video_job",
        callback_worker_method=_CALLBACK_WORKER,
        service_type=_SERVICE_TYPE,
        payload=payload,
        job_timeout=settings.job_timeout_seconds,
        result_ttl=settings.result_ttl_seconds,
        failure_ttl=settings.failure_ttl_seconds,
        retry=Retry(max=3, interval=[60, 300, 900]),
        callback_timeout_seconds=int(getattr(settings, "callback_job_timeout_seconds", 120)),
        callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
        durable_result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", settings.result_ttl_seconds)),
    )
    return {
        "job": decision.job,
        "dispatch_action": decision.outcome,
        "authoritative_generation": decision.authoritative_generation,
        "work_state": decision.work_state,
        "callback_state": decision.callback_state,
        "terminal_result_type": decision.terminal_result_type,
        "result_digest": decision.result_digest,
    }
