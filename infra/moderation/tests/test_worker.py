from __future__ import annotations

from types import SimpleNamespace

import pytest
from app import worker


def moderation_settings():
	return SimpleNamespace(
		max_text_chars=500,
		reject_terms=("blocked-term",),
		review_terms=("review-term",),
		max_media_bytes=1024,
		inspect_media=True,
	)


def test_worker_happy_path_stubs_storage_and_callback(monkeypatch):
	callbacks = []
	storage = object()
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: storage)
	monkeypatch.setattr(
		worker,
		"_inspect_image_media",
		lambda client, _item, labels, _reasons, scores: (
			labels.add("image_inspected"),
			scores.update({"image_inspected": 0.05}),
			client is storage or pytest.fail("unexpected storage boundary"),
		),
	)
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))

	result = worker.process_moderation_job(
		{
			"job_id": "job-1",
			"callback_url": "https://callback.invalid/moderation",
			"target": {"doctype": "AOS Ad", "name": "AD-1"},
			"text_items": [{"text": "ordinary listing"}],
			"media_items": [{"content_type": "image/png", "bucket": "fake", "object_key": "x"}],
		}
	)
	assert result["status"] == "completed"
	assert result["decision"] == "allow"
	assert "image_inspected" in result["labels"]
	assert callbacks[0][1]["status"] == "completed"


def test_worker_failure_reports_failure_and_reraises(monkeypatch):
	callbacks = []
	monkeypatch.setattr(
		worker, "_moderate", lambda _payload: (_ for _ in ()).throw(RuntimeError("ML failed"))
	)
	monkeypatch.setattr(worker, "_callback", lambda url, payload: callbacks.append((url, payload)))
	with pytest.raises(RuntimeError, match="ML failed"):
		worker.process_moderation_job(
			{"job_id": "job-2", "callback_url": "https://callback.invalid/moderation"}
		)
	assert callbacks[-1][1]["status"] == "failed"
