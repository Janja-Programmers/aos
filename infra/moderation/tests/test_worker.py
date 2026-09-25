from __future__ import annotations

from types import SimpleNamespace

import pytest
from app import worker


def moderation_settings():
	return SimpleNamespace(
		max_text_chars=500,
		max_text_items=16,
		max_media_bytes=1024,
		max_images=8,
		inspect_media=True,
		vision_url="http://image-search:8000/internal/moderation/classify-images",
		vision_secret="secret",
		vision_allowed_hosts=("image-search",),
		vision_timeout_seconds=10,
		environment="test",
	)


def test_work_happy_path_is_separate_from_callback(monkeypatch):
	storage = object()
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: storage)
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda client, items, settings: ([], {"vision": "fake:1"}) if client is storage else pytest.fail("unexpected storage boundary"),
	)
	result = worker._perform_moderation_work(
		{
			"job_id": "job-1",
			"policy_version": worker.POLICY_VERSION,
			"callback_url": "https://callback.invalid/moderation",
			"target": {"doctype": "AOS Ad", "name": "AD-1"},
			"text_items": [{"text": "ordinary listing"}],
			"media_items": [{"content_type": "image/png", "bucket": "fake", "object_key": "x"}],
		}
	)
	assert result["status"] == "completed"
	assert result["decision"] == "allow"
	assert result["model_versions"]["vision"] == "fake:1"


def test_work_failure_raises(monkeypatch):
	monkeypatch.setattr(
		worker, "_moderate", lambda _payload: (_ for _ in ()).throw(RuntimeError("ML failed"))
	)
	with pytest.raises(RuntimeError, match="ML failed"):
		worker._perform_moderation_work(
			{"job_id": "job-2", "callback_url": "https://callback.invalid/moderation"}
		)


def test_video_without_representative_frame_requires_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	result = worker._moderate({
		"text_items": [{"field": "caption", "text": "ordinary listing"}],
		"media_items": [{"field": "raw_video", "content_type": "video/mp4", "size_bytes": 150 * 1024 * 1024}],
	})
	assert result["decision"] == "review"
	assert "video_visual" in result["missing_evidence"]


def test_safe_text_with_unsafe_image_rejects(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda **_kwargs: ([{
			"category": "pornography",
			"confidence": 0.99,
			"severity": "critical",
			"source": "vision",
		}], {"vision": "fake:1"}),
	)
	result = worker._moderate({
		"text_items": [{"field": "caption", "text": "ordinary listing"}],
		"media_items": [{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}],
	})
	assert result["decision"] == "reject"
	assert "pornography" in result["labels"]


def test_image_provider_failure_requires_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())
	def _fail(**_kwargs):
		raise RuntimeError("provider unavailable")
	monkeypatch.setattr(worker, "classify_images", _fail)
	result = worker._moderate({
		"text_items": [{"field": "caption", "text": "ordinary listing"}],
		"media_items": [{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}],
	})
	assert result["decision"] == "review"


def test_policy_version_mismatch_fails_closed(monkeypatch):
	with pytest.raises(worker.ModerationProcessingError, match="policy version mismatch"):
		worker._perform_moderation_work({
			"job_id": "job-policy-old",
			"callback_url": "https://callback.invalid/moderation",
			"policy_version": "obsolete-policy",
		})
