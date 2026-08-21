from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from app import worker


TEST_CLASSIFICATION_SECRET = "classification-secret"  # pragma: allowlist secret


def settings():
	return SimpleNamespace(
		max_duration_seconds=180,
		minio_public_base_url="https://files.invalid",
		output_bucket="processed",
		output_base_path="shorts",
		thumbnail_bucket="media",
		thumbnail_base_path="thumbnails",
		max_input_bytes=536870912,
		max_width=4096,
		max_height=4096,
		max_pixels=16777216,
		min_aspect_ratio=0.25,
		max_aspect_ratio=4.0,
		allowed_video_codecs=("h264", "hevc", "vp8", "vp9", "av1", "mpeg4"),
		ffprobe_timeout_seconds=30,
		ffmpeg_timeout_seconds=1800,
		ffmpeg_threads=2,
		environment="test",
		callback_allowed_hosts=(),
		callback_secret="test-secret",
		callback_timeout_seconds=30,
		classification_enabled=True,
		classification_url="http://image-search:8000/internal/shorts/classify-frames",
		classification_secret=TEST_CLASSIFICATION_SECRET,
		classification_allowed_hosts=("image-search",),
		classification_timeout_seconds=30,
		classification_frame_count=5,
		classification_max_frame_bytes=1048576,
	)


def payload():
	return {
		"job_id": "job-1",
		"short_id": "SHORT-2026-00001",
		"callback_url": "https://callback.invalid/video",
		"raw_video": {"bucket": "raw", "object_key": "clip.mp4", "size_bytes": 1024},
		"output": {
			"output_bucket": "shorts",
			"output_base_path": "shorts/processed",
			"thumbnail_bucket": "aos-public",
			"thumbnail_base_path": "shorts/thumbnails",
			"max_duration_seconds": 180,
		},
	}


def configure_boundaries(monkeypatch, tmp_path):
	work_dir = tmp_path / "worker"
	work_dir.mkdir()
	uploads = []
	monkeypatch.setattr(worker, "get_settings", settings)
	monkeypatch.setattr(worker, "_minio_client", object)
	monkeypatch.setattr(worker.tempfile, "mkdtemp", lambda **_kwargs: str(work_dir))
	monkeypatch.setattr(worker.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed-version"))
	monkeypatch.setattr(
		worker,
		"_probe_video",
		lambda _path: {"duration": 2.5, "width": 320, "height": 480, "codec": "h264"},
	)
	monkeypatch.setattr(
		worker,
		"_download_object",
		lambda _client, *, destination, **_kwargs: Path(destination).write_bytes(b"video"),
	)
	monkeypatch.setattr(worker, "_has_audio", lambda _path: False)
	monkeypatch.setattr(
		worker,
		"_generate_thumbnail",
		lambda _source, destination: Path(destination).write_bytes(b"jpeg") and (320, 480),
	)
	monkeypatch.setattr(
		worker,
		"_generate_classification_frames",
		lambda _source, _directory, _duration, thumbnail: [thumbnail],
	)
	monkeypatch.setattr(
		worker,
		"_classify_frames",
		lambda _frames: {
			"status": "ready",
			"mode": "learn",
			"confidence": 0.9,
			"scores": {"shop": 0.02, "geo": 0.03, "vibes": 0.05, "learn": 0.9},
			"model": "synthetic",
			"model_version": "test-v1",
			"frame_count": 1,
		},
	)
	monkeypatch.setattr(
		worker,
		"_generate_mp4_original",
		lambda _source, destination, _duration: Path(destination).write_bytes(b"mp4"),
	)
	monkeypatch.setattr(
		worker,
		"_generate_hls",
		lambda _source, directory: Path(directory, "master.m3u8").write_text("#EXTM3U\n", encoding="utf-8"),
	)
	monkeypatch.setattr(
		worker,
		"_upload_file",
		lambda _client, **kwargs: (
			uploads.append(kwargs)
			or {
				"bucket": kwargs["bucket"],
				"object_key": kwargs["object_key"],
				"size_bytes": 5,
				"etag": "test-etag",
				"content_type": kwargs["content_type"],
			}
		),
	)
	return work_dir, uploads


def test_work_happy_path_is_separate_from_callback(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	result = worker._perform_video_work(payload())
	assert result["job_id"] == "job-1"
	assert result["status"] == "ready"
	assert result["duration_seconds"] == 2.5
	assert result["classification"]["mode"] == "learn"
	assert result["output_object_count"] >= 2
	assert "objects" not in result
	assert len(uploads) >= 3
	assert not work_dir.exists()


def test_work_failure_raises_and_cleans_tempdir(monkeypatch, tmp_path):
	work_dir, _uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(
		worker,
		"_download_object",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage failed")),
	)
	with pytest.raises(RuntimeError, match="storage failed"):
		worker._perform_video_work(payload())
	assert not work_dir.exists()


def test_classification_failure_is_non_fatal(monkeypatch, tmp_path):
	work_dir, _uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(
		worker,
		"_classify_frames",
		lambda _frames: worker._classification_fallback("unavailable"),
	)
	result = worker._perform_video_work(payload())
	assert result["status"] == "ready"
	assert result["classification"]["status"] == "unavailable"
	assert not work_dir.exists()


def test_classification_request_is_timestamp_signed(monkeypatch, tmp_path):
	frame = tmp_path / "frame.jpg"
	frame.write_bytes(b"synthetic-jpeg")
	captured = {}

	class Response:
		content = b"{}"

		def raise_for_status(self):
			return None

		def json(self):
			return {
				"status": "ready",
				"mode": "learn",
				"confidence": 0.9,
				"scores": {"shop": 0.02, "geo": 0.03, "vibes": 0.05, "learn": 0.9},
				"model": "synthetic",
				"model_version": "test-v1",
			}

	def post(url, *, data, headers, timeout):
		captured.update(url=url, data=data, headers=headers, timeout=timeout)
		return Response()

	monkeypatch.setattr(worker, "get_settings", settings)
	monkeypatch.setattr(worker.time, "time", lambda: 1700000000.0)
	monkeypatch.setattr(worker.requests, "post", post)
	result = worker._classify_frames([str(frame)])
	assert result["mode"] == "learn"
	assert captured["headers"]["X-AOS-Timestamp"] == "1700000000"
	signed = b"1700000000." + captured["data"]
	assert captured["headers"]["X-AOS-Signature"] == worker.build_signature(
		TEST_CLASSIFICATION_SECRET, signed
	)


def test_original_audio_is_extracted_and_returned_for_reuse(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(worker, "_has_audio", lambda _path: True)
	monkeypatch.setattr(
		worker,
		"_extract_original_audio",
		lambda _source, destination, _duration: Path(destination).write_bytes(b"audio"),
	)

	result = worker._perform_video_work(payload())

	audio = result["original_audio"]
	assert audio["bucket"] == "aos-public"
	assert audio["object_key"].endswith(
		"sounds/uploads/original/SHORT-2026-00001/fixed-version/original.m4a"
	)
	assert audio["content_type"] == "audio/mp4"
	assert audio["duration_seconds"] == 2.5
	assert audio["size_bytes"] == 5
	assert any(item.get("content_type") == "audio/mp4" for item in uploads)
	assert not work_dir.exists()


def test_selected_sound_mixes_with_original_audio(monkeypatch, tmp_path):
	captured = {}
	input_path = tmp_path / "input.mp4"
	sound_path = tmp_path / "sound.mp3"
	output_path = tmp_path / "output.mp4"
	input_path.write_bytes(b"video")
	sound_path.write_bytes(b"sound")

	monkeypatch.setattr(worker, "_has_audio", lambda _path: True)
	monkeypatch.setattr(
		worker,
		"_run",
		lambda cmd, _message, **_kwargs: captured.update(cmd=cmd),
	)

	worker._generate_mp4_with_sound(
		str(input_path),
		str(sound_path),
		str(output_path),
		12.0,
		{"start_ms": 1000, "duration_ms": 8000, "volume": 0.6},
	)

	filter_graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
	assert "[0:a]" in filter_graph
	assert "[1:a]volume=0.6" in filter_graph
	assert "amix=inputs=2" in filter_graph
	assert "duration=longest" in filter_graph


def test_selected_sound_is_only_audio_when_video_is_silent(monkeypatch, tmp_path):
	captured = {}
	input_path = tmp_path / "input.mp4"
	sound_path = tmp_path / "sound.mp3"
	output_path = tmp_path / "output.mp4"
	input_path.write_bytes(b"video")
	sound_path.write_bytes(b"sound")

	monkeypatch.setattr(worker, "_has_audio", lambda _path: False)
	monkeypatch.setattr(
		worker,
		"_run",
		lambda cmd, _message, **_kwargs: captured.update(cmd=cmd),
	)

	worker._generate_mp4_with_sound(
		str(input_path),
		str(sound_path),
		str(output_path),
		5.0,
		{"volume": 1.0},
	)

	filter_graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
	assert "[0:a]" not in filter_graph
	assert "amix=" not in filter_graph
	assert "[1:a]volume=1.0" in filter_graph


def test_audio_reprocess_preserves_visual_metadata(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	audio_payload = payload()
	audio_payload.update(
		{
			"force": True,
			"reason": "audio_reprocess",
			"sound": {
				"bucket": "sounds",
				"object_key": "sounds/uploads/track.mp3",
				"volume": 0.8,
			},
		}
	)
	monkeypatch.setattr(
		worker,
		"_generate_thumbnail",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(
			AssertionError("audio reprocess must not regenerate a thumbnail")
		),
	)
	monkeypatch.setattr(
		worker,
		"_generate_classification_frames",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(
			AssertionError("audio reprocess must not rerun visual classification")
		),
	)
	monkeypatch.setattr(
		worker,
		"_generate_mp4_with_sound",
		lambda _video, _sound, destination, _duration, _settings: Path(destination).write_bytes(b"mixed"),
	)

	result = worker._perform_video_work(audio_payload)

	assert result["status"] == "ready"
	assert result["reason"] == "audio_reprocess"
	assert result["sound_applied"] is True
	assert "original_audio" not in result
	assert "thumbnail" not in result
	assert "classification" not in result
	assert not any(item.get("content_type") == "image/jpeg" for item in uploads)
	assert not work_dir.exists()
