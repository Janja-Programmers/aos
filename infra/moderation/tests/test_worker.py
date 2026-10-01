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
		semantic_text_enabled=False,
		semantic_text_required=False,
		semantic_text_url="http://text-safety:8000/internal/moderation/classify-text",
		semantic_text_ready_url="http://text-safety:8000/ready",
		semantic_text_secret="semantic-secret",
		semantic_text_allowed_hosts=("text-safety",),
		semantic_text_timeout_seconds=5,
		vision_url="http://image-search:8000/internal/moderation/classify-images",
		vision_secret="secret",
		vision_allowed_hosts=("image-search",),
		vision_timeout_seconds=10,
		vision_max_image_bytes=786432,
		vision_max_total_bytes=4194304,
		vision_max_dimension=1024,
		vision_max_pixels=25000000,
		environment="test",
	)


def test_work_happy_path_is_separate_from_callback(monkeypatch):
	storage = object()
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: storage)
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda client, items, settings: (
			([], {"vision": "fake:1"}, [])
			if client is storage
			else pytest.fail("unexpected storage boundary")
		),
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
			{
				"job_id": "job-2",
				"policy_version": worker.POLICY_VERSION,
				"callback_url": "https://callback.invalid/moderation",
			}
		)


def test_video_without_representative_frame_requires_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "ordinary listing"}],
			"media_items": [
				{"field": "raw_video", "content_type": "video/mp4", "size_bytes": 150 * 1024 * 1024}
			],
		}
	)
	assert result["decision"] == "review"
	assert "video_visual" in result["missing_evidence"]


def test_safe_text_with_unsafe_image_rejects(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda **_kwargs: (
			[
				{
					"category": "pornography",
					"confidence": 0.99,
					"severity": "critical",
					"source": "vision",
				}
			],
			{"vision": "fake:1"},
			[],
		),
	)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "ordinary listing"}],
			"media_items": [
				{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}
			],
		}
	)
	assert result["decision"] == "reject"
	assert "pornography" in result["labels"]


def test_image_provider_failure_requires_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())

	def _fail(**_kwargs):
		raise RuntimeError("provider unavailable")

	monkeypatch.setattr(worker, "classify_images", _fail)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "ordinary listing"}],
			"media_items": [
				{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}
			],
		}
	)
	assert result["decision"] == "review"


def test_policy_version_mismatch_fails_closed(monkeypatch):
	with pytest.raises(worker.ModerationProcessingError, match="policy version mismatch"):
		worker._perform_moderation_work(
			{
				"job_id": "job-policy-old",
				"callback_url": "https://callback.invalid/moderation",
				"policy_version": "obsolete-policy",
			}
		)


def test_near_tie_vision_uncertainty_does_not_force_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda **_kwargs: (
			[
				{
					"category": "nudity",
					"confidence": 0.132396,
					"severity": "medium",
					"source": "image",
				}
			],
			{"vision": "fake:1"},
			[
				{
					"top_category": "nudity",
					"top_confidence": 0.132396,
					"safe_confidence": 0.128804,
					"margin": 0.003592,
				}
			],
		),
	)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "ordinary listing"}],
			"media_items": [
				{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}
			],
		}
	)
	assert result["decision"] == "allow"
	assert result["scores"]["nudity"] == 0.132396


def test_meaningful_vision_uncertainty_requires_review(monkeypatch):
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	monkeypatch.setattr(worker, "_minio_client", lambda: object())
	monkeypatch.setattr(
		worker,
		"classify_images",
		lambda **_kwargs: (
			[
				{
					"category": "weapons",
					"confidence": 0.24,
					"severity": "medium",
					"source": "image",
				}
			],
			{"vision": "fake:1"},
			[
				{
					"top_category": "weapons",
					"top_confidence": 0.24,
					"safe_confidence": 0.18,
					"margin": 0.06,
				}
			],
		),
	)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "ordinary listing"}],
			"media_items": [
				{"field": "image", "content_type": "image/png", "bucket": "fake", "object_key": "x"}
			],
		}
	)
	assert result["decision"] == "review"
	assert result["reasons"] == ["vision uncertainty: weapons meaningfully outranked safe"]


def test_semantic_text_signal_can_reject_without_keyword_rule(monkeypatch):
	settings = moderation_settings()
	settings.semantic_text_enabled = True
	settings.semantic_text_required = True
	monkeypatch.setattr(worker, "get_settings", lambda: settings)
	monkeypatch.setattr(
		worker,
		"classify_text",
		lambda **_kwargs: (
			[
				{
					"category": "pornography",
					"confidence": 0.97,
					"severity": "critical",
					"source": "text",
					"field": "comment",
					"detector": "semantic-test",
					"detector_version": "1",
				}
			],
			{"text_semantic": "semantic-test:1"},
		),
	)
	result = worker._moderate(
		{"text_items": [{"field": "comment", "text": "context not covered by rules"}], "media_items": []}
	)
	assert result["decision"] == "reject"
	assert result["scores"]["pornography"] == 0.97
	assert result["model_versions"]["text_semantic"] == "semantic-test:1"


def test_required_semantic_provider_failure_never_allows(monkeypatch):
	settings = moderation_settings()
	settings.semantic_text_enabled = True
	settings.semantic_text_required = True
	monkeypatch.setattr(worker, "get_settings", lambda: settings)
	monkeypatch.setattr(
		worker, "classify_text", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("down"))
	)
	result = worker._moderate(
		{"text_items": [{"field": "comment", "text": "ordinary review"}], "media_items": []}
	)
	assert result["decision"] == "review"
	assert "detector failure: semantic_text" in result["reasons"]


def test_contextual_pornography_sale_rejects_without_semantic_provider(monkeypatch):
	settings = moderation_settings()
	settings.semantic_text_enabled = False
	settings.semantic_text_required = False
	monkeypatch.setattr(worker, "get_settings", lambda: settings)
	result = worker._moderate(
		{
			"text_items": [
				{"field": "title", "text": "Dm for porno video"},
				{"field": "comment", "text": "I have porn videos for sale"},
			],
			"media_items": [],
		}
	)
	assert result["decision"] == "reject"
	assert result["scores"]["pornography"] == 0.97
	assert result["model_versions"]["text"] == "aos_text_rules:6"


def test_weapon_sale_rejects_even_when_semantic_model_is_noisy(monkeypatch):
	settings = moderation_settings()
	settings.semantic_text_enabled = True
	settings.semantic_text_required = True
	monkeypatch.setattr(worker, "get_settings", lambda: settings)
	monkeypatch.setattr(
		worker,
		"classify_text",
		lambda **_kwargs: (
			[
				{
					"category": "pornography",
					"confidence": 0.569405,
					"severity": "critical",
					"source": "text",
					"field": "caption",
					"reason": "semantic_text_classifier",
				},
				{
					"category": "threats",
					"confidence": 0.569245,
					"severity": "critical",
					"source": "text",
					"field": "caption",
					"reason": "semantic_text_classifier",
				},
				{
					"category": "weapons",
					"confidence": 0.383221,
					"severity": "high",
					"source": "text",
					"field": "caption",
					"reason": "semantic_text_classifier",
				},
			],
			{"text_semantic": "semantic-test:noise"},
		),
	)
	result = worker._moderate(
		{
			"text_items": [{"field": "caption", "text": "Guns for sale. DM me to buy."}],
			"media_items": [],
		}
	)
	assert result["decision"] == "reject"
	assert result["scores"]["weapons"] == 0.98
	assert result["scores"]["illegal_goods"] == 0.97
	assert set(result["labels"]) == {"illegal_goods", "weapons"}
	assert result["risk_score"] == 0.98
