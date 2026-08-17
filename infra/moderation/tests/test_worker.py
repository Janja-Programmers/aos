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


def test_work_happy_path_is_separate_from_callback(monkeypatch):
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
	result = worker._perform_moderation_work(
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


def test_work_failure_raises(monkeypatch):
	monkeypatch.setattr(
		worker, "_moderate", lambda _payload: (_ for _ in ()).throw(RuntimeError("ML failed"))
	)
	with pytest.raises(RuntimeError, match="ML failed"):
		worker._perform_moderation_work(
			{"job_id": "job-2", "callback_url": "https://callback.invalid/moderation"}
		)


def test_large_video_is_not_rejected_by_image_inspection_byte_limit(monkeypatch):
	"""Ad videos follow Media's video policy; this worker only byte-inspects images."""
	monkeypatch.setattr(worker, "get_settings", moderation_settings)
	labels: set[str] = set()
	reasons: list[str] = []
	scores: dict[str, float] = {}

	worker._inspect_image_media(
		object(),
		{
			"media_id": "MEDIA-VIDEO-1",
			"content_type": "video/mp4",
			"size_bytes": 150 * 1024 * 1024,
		},
		labels,
		reasons,
		scores,
	)

	assert "video_present" in labels
	assert "media_too_large_for_moderation" not in labels
	assert not reasons
