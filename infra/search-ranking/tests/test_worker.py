from __future__ import annotations

import pytest
from app import worker


def payload():
	return {
		"job_id": "job-1",
		"callback_url": "https://callback.invalid/search",
		"target": {"doctype": "AOS Ad", "name": "AD-1"},
		"document": {"name": "AD-1", "title": "Synthetic listing"},
	}


def test_worker_happy_path_stubs_redis_and_callback(monkeypatch):
	redis = object()
	indexed = []
	callbacks = []
	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(worker, "upsert_ad", lambda client, document: indexed.append((client, document)))
	monkeypatch.setattr(worker, "_callback", lambda url, result: callbacks.append((url, result)))

	result = worker.process_search_ranking_job(payload())
	assert result["status"] == "completed"
	assert indexed == [(redis, payload()["document"])]
	assert callbacks[0][1]["status"] == "completed"


def test_worker_failure_reports_failure_and_reraises(monkeypatch):
	callbacks = []
	monkeypatch.setattr(worker, "get_redis", object)
	monkeypatch.setattr(
		worker, "upsert_ad", lambda *_args: (_ for _ in ()).throw(RuntimeError("redis failed"))
	)
	monkeypatch.setattr(worker, "_callback", lambda url, result: callbacks.append((url, result)))
	with pytest.raises(RuntimeError, match="redis failed"):
		worker.process_search_ranking_job(payload())
	assert callbacks[-1][1]["status"] == "failed"
