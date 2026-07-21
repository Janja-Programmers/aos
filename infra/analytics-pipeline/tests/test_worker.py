from __future__ import annotations

from types import SimpleNamespace

from app import worker


def worker_settings():
	return SimpleNamespace(
		max_events_per_job=10,
		stream_max_len=100,
		callback_secret="unit-callback",
		event_dedupe_ttl_seconds=2592000,
	)


def test_analytics_events_are_applied_effectively_once(monkeypatch, redis_conn):
	monkeypatch.setattr(worker, "get_settings", worker_settings)
	monkeypatch.setattr(worker, "get_redis", lambda: redis_conn)

	def eval_script(_script, numkeys, *args):
		keys = list(args[:numkeys])
		argv = list(args[numkeys:])
		processed_key = keys[0]
		if redis_conn.exists(processed_key):
			return 0
		redis_conn.set(processed_key, "1", ex=int(argv[0]))
		redis_conn.xadd(keys[1], {"event_type": argv[2], "payload": argv[3]})
		for counter_key in keys[2:7]:
			if counter_key != "-":
				redis_conn.hincrby(counter_key, argv[2], 1)
		metric_count = int(argv[4])
		offset = 5
		for _ in range(metric_count):
			redis_conn.hincrby(keys[7], argv[offset], int(argv[offset + 1]))
			offset += 2
		return 1

	monkeypatch.setattr(redis_conn, "eval", eval_script)
	payload = {
		"job_id": "job-1",
		"idempotency_key": "stable-analytics-event-job",
		"callback_url": "https://callback.invalid/analytics",
		"events": [{"event_id": "EVENT-1", "event_type": "view", "event_date": "2026-07-18"}, {}],
	}
	first = worker._perform_analytics_work(payload)
	second = worker._perform_analytics_work(payload)
	assert first["ingested_count"] == 1
	assert first["skipped_count"] == 1
	assert second["ingested_count"] == 0
	assert second["deduplicated_count"] == 1
	assert int(redis_conn.hget("aos:analytics:day:2026-07-18", "view") or 0) == 1


def test_explicit_event_id_deduplicates_across_different_jobs(monkeypatch, redis_conn):
	monkeypatch.setattr(worker, "get_settings", worker_settings)
	monkeypatch.setattr(worker, "get_redis", lambda: redis_conn)

	def eval_script(_script, numkeys, *args):
		keys = list(args[:numkeys])
		argv = list(args[numkeys:])
		if redis_conn.exists(keys[0]):
			return 0
		redis_conn.set(keys[0], "1", ex=int(argv[0]))
		redis_conn.hincrby(keys[2], argv[2], 1)
		return 1

	monkeypatch.setattr(redis_conn, "eval", eval_script)
	base_event = {"event_id": "GLOBAL-EVENT-1", "event_type": "purchase", "event_date": "2026-07-20"}
	first = worker._perform_analytics_work(
		{"job_id": "job-a", "idempotency_key": "stable-a", "events": [base_event]}
	)
	second = worker._perform_analytics_work(
		{"job_id": "job-b", "idempotency_key": "stable-b", "events": [{"event_type": "noop"}, base_event]}
	)
	assert first["ingested_count"] == 1
	assert second["deduplicated_count"] == 1
	assert int(redis_conn.hget("aos:analytics:day:2026-07-20", "purchase") or 0) == 1


def test_fallback_identity_ignores_generated_timestamp(monkeypatch):
	raw = {"event_type": "view", "event_date": "2026-07-20", "metadata": {"slot": "home"}}
	first = worker._normalize_event(raw)
	second = worker._normalize_event(raw)
	assert first is not None and second is not None
	first["occurred_at"] = "2026-07-20T00:00:01Z"
	second["occurred_at"] = "2026-07-20T00:00:59Z"
	assert worker._event_identity("stable-job", 0, first, raw) == worker._event_identity(
		"stable-job", 0, second, raw
	)
