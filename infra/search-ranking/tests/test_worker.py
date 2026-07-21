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


def test_work_happy_path_is_separate_from_callback(monkeypatch):
	redis = object()
	indexed = []
	monkeypatch.setattr(worker, "get_redis", lambda: redis)
	monkeypatch.setattr(worker, "upsert_ad", lambda client, document: indexed.append((client, document)))
	result = worker._perform_search_work(payload())
	assert result["status"] == "completed"
	assert indexed == [(redis, payload()["document"])]


def test_work_failure_raises(monkeypatch):
	monkeypatch.setattr(worker, "get_redis", object)
	monkeypatch.setattr(worker, "upsert_ad", lambda *_args: (_ for _ in ()).throw(RuntimeError("redis failed")))
	with pytest.raises(RuntimeError, match="redis failed"):
		worker._perform_search_work(payload())
