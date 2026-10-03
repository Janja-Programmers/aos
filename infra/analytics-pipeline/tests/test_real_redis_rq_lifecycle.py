from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from app import durable_lifecycle as lifecycle
from app import worker
from app.durable_lifecycle import enqueue_or_reconcile, load_result
from redis import Redis
from rq import Queue, Retry, SimpleWorker
from rq.registry import ScheduledJobRegistry


@pytest.mark.integration
def test_real_redis_rq_separates_work_from_callback_delivery(monkeypatch):
	redis_url = os.getenv("AOS_TEST_REDIS_URL", "").strip()
	if not redis_url:
		pytest.skip("AOS_TEST_REDIS_URL is not configured; real Redis/RQ validation is CI-only.")
	redis = Redis.from_url(redis_url)
	redis.ping()
	redis.flushdb()
	queue = Queue(f"lifecycle-analytics_ingestion-{uuid.uuid4().hex[:8]}", connection=redis)
	stable_id = "stable-real-rq-job-1"
	work_counter = "test:work-count:analytics_ingestion"

	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(worker, "get_queue", lambda: queue)
	monkeypatch.setattr(
		worker,
		"_perform_analytics_work",
		lambda _payload: redis.incr(work_counter) and {"job_id": "service-job-1", "status": "ingested"},
	)
	monkeypatch.setattr(
		worker,
		"_callback",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("callback unavailable")),
	)
	payload = {
		"job_id": "service-job-1",
		"idempotency_key": stable_id,
		"dispatch_id": "stable-dispatch-1",
		"dispatch_generation": 1,
		"dispatch_token": "a" * 32,
		"callback_url": "https://callback.invalid/result",
	}
	decision = enqueue_or_reconcile(
		redis=redis,
		queue=queue,
		worker_method="app.worker.process_analytics_ingest_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type="analytics_ingestion",
		payload=payload,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
	)
	assert decision.outcome == "enqueued"
	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	work_job = queue.fetch_job(decision.job.id)
	assert (
		str(getattr(work_job.get_status(refresh=True), "value", work_job.get_status())).lower() == "finished"
	)
	record = load_result(redis, "analytics_ingestion", stable_id)
	assert record["work_state"] == "work_complete"
	assert record["callback_status"] == "pending"
	assert int(redis.get(work_counter) or 0) == 1

	# The independently queued callback job fails without altering the finished
	# work job or re-running its external side effect.
	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	assert int(redis.get(work_counter) or 0) == 1
	assert load_result(redis, "analytics_ingestion", stable_id)["work_state"] == "work_complete"

	record = load_result(redis, "analytics_ingestion", stable_id)
	callback_job_id = record["callback_job_id"]
	registry = ScheduledJobRegistry(queue=queue)
	assert callback_job_id in registry.get_job_ids()

	# Prove the retry uses RQ's scheduled registry rather than a direct manual
	# callback invocation. Requeue the scheduled retry after repairing transport.
	monkeypatch.setattr(
		worker,
		"_callback",
		lambda *_args, **_kwargs: SimpleNamespace(status_code=200, json=lambda: {"ok": True}),
	)
	registry.requeue(callback_job_id)
	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	completed = load_result(redis, "analytics_ingestion", stable_id)
	assert completed["callback_status"] == "complete"
	assert int(redis.get(work_counter) or 0) == 1


@pytest.mark.integration
def test_real_redis_lua_applies_analytics_event_once(monkeypatch):
	redis_url = os.getenv("AOS_TEST_REDIS_URL", "").strip()
	if not redis_url:
		pytest.skip("AOS_TEST_REDIS_URL is not configured; real Redis analytics validation is CI-only.")
	redis = Redis.from_url(redis_url)
	redis.ping()
	redis.flushdb()
	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(
		worker,
		"get_settings",
		lambda: SimpleNamespace(
			max_events_per_job=10,
			stream_max_len=100,
			event_dedupe_ttl_seconds=2592000,
			aggregate_retention_seconds=34560000,
			callback_secret="test-callback-secret",
		),
	)
	payload = {
		"job_id": "analytics-job-real-dedupe",
		"idempotency_key": "stable-analytics-real-dedupe",
		"events": [
			{
				"event_id": "EVENT-REAL-1",
				"event_type": "ad_detail_view",
				"event_date": "2026-07-20",
				"metrics": {"watch_seconds": 3},
			}
		],
	}
	first = worker._perform_analytics_work(payload)
	second = worker._perform_analytics_work(payload)
	assert first["ingested_count"] == 1
	assert second["ingested_count"] == 0
	assert second["deduplicated_count"] == 1
	assert int(redis.hget("aos:analytics:day:2026-07-20", "ad_detail_view") or 0) == 1
	assert int(redis.hget("aos:analytics:metric:2026-07-20", "watch_seconds") or 0) == 3


@pytest.mark.integration
def test_real_redis_rq_retries_transient_work_before_terminal_result(monkeypatch):
	redis_url = os.getenv("AOS_TEST_REDIS_URL", "").strip()
	if not redis_url:
		pytest.skip("AOS_TEST_REDIS_URL is not configured; real Redis/RQ validation is CI-only.")
	redis = Redis.from_url(redis_url)
	redis.ping()
	redis.flushdb()
	queue = Queue(f"retry-analytics_ingestion-{uuid.uuid4().hex[:8]}", connection=redis)
	stable_id = "stable-real-rq-retry-1"
	attempt_counter = "test:work-attempt:analytics_ingestion"
	effect_counter = "test:work-effect:analytics_ingestion"
	callback_counter = "test:callback-count:analytics_ingestion"

	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(worker, "get_queue", lambda: queue)

	def transient_then_success(_payload):
		attempt = int(redis.incr(attempt_counter))
		if attempt == 1:
			raise lifecycle.RetryableWorkError("temporary dependency failure")
		redis.incr(effect_counter)
		return {"job_id": "service-job-retry-1", "status": "ingested"}

	monkeypatch.setattr(worker, "_perform_analytics_work", transient_then_success)
	monkeypatch.setattr(
		worker,
		"_callback",
		lambda *_args, **_kwargs: (
			redis.incr(callback_counter) and SimpleNamespace(status_code=200, json=lambda: {"ok": True})
		),
	)
	payload = {
		"job_id": "service-job-retry-1",
		"idempotency_key": stable_id,
		"dispatch_id": "stable-dispatch-retry-1",
		"dispatch_generation": 1,
		"dispatch_token": "c" * 32,
		"callback_url": "https://callback.invalid/result",
	}
	decision = enqueue_or_reconcile(
		redis=redis,
		queue=queue,
		worker_method="app.worker.process_analytics_ingest_job",
		callback_worker_method="app.worker.deliver_callback_job",
		service_type="analytics_ingestion",
		payload=payload,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1, interval=[1]),
		callback_timeout_seconds=120,
		callback_max_attempts=3,
		durable_result_ttl_seconds=604800,
	)
	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	record = load_result(redis, "analytics_ingestion", stable_id)
	assert record["work_state"] == "retrying"
	assert int(redis.get(effect_counter) or 0) == 0
	registry = ScheduledJobRegistry(queue=queue)
	assert decision.job.id in registry.get_job_ids()

	registry.requeue(decision.job.id)
	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	record = load_result(redis, "analytics_ingestion", stable_id)
	assert record["work_state"] == "work_complete"
	assert int(redis.get(attempt_counter) or 0) == 2
	assert int(redis.get(effect_counter) or 0) == 1

	SimpleWorker([queue], connection=redis).work(burst=True, max_jobs=1, with_scheduler=False)
	assert load_result(redis, "analytics_ingestion", stable_id)["callback_status"] == "complete"
	assert int(redis.get(callback_counter) or 0) == 1
	assert int(redis.get(effect_counter) or 0) == 1
