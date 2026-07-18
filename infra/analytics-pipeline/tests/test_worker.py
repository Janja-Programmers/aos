from __future__ import annotations

from types import SimpleNamespace

import pytest
from app import worker


class FakeRedis:
	def __init__(self):
		self.events = []
		self.counters = []

	def xadd(self, key, payload, **options):
		self.events.append((key, payload, options))

	def hincrby(self, key, field, amount):
		self.counters.append((key, field, amount))


def worker_settings():
	return SimpleNamespace(max_events_per_job=10, stream_max_len=100, callback_secret="unit-callback")


def test_worker_ingests_events_without_network(monkeypatch):
	redis = FakeRedis()
	callbacks = []
	monkeypatch.setattr(worker, "get_settings", worker_settings)
	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))

	result = worker.process_analytics_ingest_job(
		{
			"job_id": "job-1",
			"callback_url": "https://callback.invalid/analytics",
			"events": [{"event_type": "view", "event_date": "2026-07-18"}, {}],
		}
	)

	assert result["status"] == "ingested"
	assert result["ingested_count"] == 1
	assert result["skipped_count"] == 1
	assert redis.events[0][0] == "aos:analytics:events"
	assert callbacks[0][1]["status"] == "ingested"


def test_worker_failure_reports_failure_and_reraises(monkeypatch):
	callbacks = []
	monkeypatch.setattr(worker, "get_settings", worker_settings)
	monkeypatch.setattr(worker, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("redis unavailable")))
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))

	with pytest.raises(RuntimeError, match="redis unavailable"):
		worker.process_analytics_ingest_job(
			{
				"job_id": "job-2",
				"callback_url": "https://callback.invalid/analytics",
				"events": [{"event_type": "view"}],
			}
		)
	assert callbacks[-1][1]["status"] == "failed"
