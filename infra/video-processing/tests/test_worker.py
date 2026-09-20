from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from app import worker


TEST_CLASSIFICATION_SECRET = "classification-secret"  # pragma: allowlist secret
SHORT_ID = "SHR-AAAAAAAAAAAAAAAAAAAA"
JOB_ID = "a1b2c3d4e5"


def settings():
	return SimpleNamespace(
		max_duration_seconds=600,
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


def payload(*, operation: str = "Process") -> dict:
	return {
		"job_id": JOB_ID,
		"idempotency_key": "stable-video-job-key",
		"job_generation": 1,
		"short_id": SHORT_ID,
		"operation": operation,
		"callback_url": "https://callback.invalid/video",
		"raw_video": {"bucket": "raw", "object_key": "shorts/raw/clip.mp4", "size_bytes": 1024},
		"output": {
			"playback_bucket": "aos-public",
			"playback_base_path": "shorts/playback",
			"poster_bucket": "aos-public",
			"poster_base_path": "shorts/posters",
			"storyboard_bucket": "aos-public",
			"storyboard_base_path": "shorts/storyboards",
			"download_bucket": "aos-private",
			"download_base_path": "shorts/downloads",
			"sound_bucket": "aos-public",
			"sound_base_path": "shorts/original-audio",
			"max_duration_seconds": 600,
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
	def storyboard(_source, image_path, manifest_path, _duration):
		Path(image_path).write_bytes(b"jpeg")
		Path(manifest_path).write_text('{"frames":[]}', encoding="utf-8")
		return 640, 360
	monkeypatch.setattr(worker, "_generate_storyboard", storyboard)
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


def test_process_returns_canonical_outputs(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	result = worker._perform_video_work(payload())
	assert result["job_id"] == JOB_ID
	assert result["short_id"] == SHORT_ID
	assert result["status"] == "ready"
	assert result["duration_seconds"] == 2.5
	assert result["classification"]["mode"] == "learn"
	assert {"playback", "manifest", "poster", "storyboard", "storyboard_manifest"} <= set(result["outputs"])
	assert result["outputs"]["manifest"]["object_key"].endswith("/hls/master.m3u8")
	assert len(uploads) >= 5
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
	monkeypatch.setattr(worker, "_classify_frames", lambda _frames: worker._classification_fallback("unavailable"))
	result = worker._perform_video_work(payload())
	assert result["status"] == "ready"
	assert result["classification"]["status"] == "unavailable"
	assert not work_dir.exists()


def test_original_audio_is_returned_as_canonical_output(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(worker, "_has_audio", lambda _path: True)
	monkeypatch.setattr(
		worker,
		"_extract_original_audio",
		lambda _source, destination, _duration: Path(destination).write_bytes(b"audio"),
	)
	result = worker._perform_video_work(payload())
	audio = result["outputs"]["original_audio"]
	assert audio["bucket"] == "aos-public"
	assert audio["object_key"].endswith(f"/{SHORT_ID}/fixed-version/original.m4a")
	assert audio["content_type"] == "audio/mp4"
	assert audio["duration_seconds"] == 2.5
	assert any(item.get("content_type") == "audio/mp4" for item in uploads)
	assert not work_dir.exists()


def test_download_operation_only_returns_private_download_output(monkeypatch, tmp_path):
	work_dir, _uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(
		worker,
		"_generate_watermarked_download",
		lambda _source, destination, _duration: Path(destination).write_bytes(b"watermarked"),
	)
	result = worker._perform_video_work(payload(operation="Download"))
	assert set(result["outputs"]) == {"download"}
	assert result["outputs"]["download"]["bucket"] == "aos-private"
	assert result["outputs"]["download"]["object_key"].endswith("/AOS.mp4")
	assert "classification" not in result
	assert not work_dir.exists()


def test_side_by_side_uses_source_video_and_normal_processing(monkeypatch, tmp_path):
	work_dir, _uploads = configure_boundaries(monkeypatch, tmp_path)
	data = payload(operation="Side By Side")
	data["source_video"] = {"bucket": "public", "object_key": "shorts/playback/source.mp4", "size_bytes": 1024}
	monkeypatch.setattr(
		worker,
		"_compose_side_by_side",
		lambda _source, _creator, destination, _max_duration: Path(destination).write_bytes(b"composed"),
	)
	result = worker._perform_video_work(data)
	assert result["status"] == "ready"
	assert "playback" in result["outputs"]
	assert not work_dir.exists()


def test_segment_reuse_uses_bounded_source_segment(monkeypatch, tmp_path):
	work_dir, _uploads = configure_boundaries(monkeypatch, tmp_path)
	data = payload(operation="Segment")
	data.update(
		{
			"source_video": {"bucket": "public", "object_key": "shorts/playback/source.mp4", "size_bytes": 1024},
			"source_start_ms": 1000,
			"source_end_ms": 4000,
		}
	)
	captured = {}
	monkeypatch.setattr(
		worker,
		"_compose_segment",
		lambda _source, _creator, destination, start_ms, end_ms, _max_duration, _work_dir: (
			captured.update(start_ms=start_ms, end_ms=end_ms), Path(destination).write_bytes(b"composed")
		),
	)
	result = worker._perform_video_work(data)
	assert result["status"] == "ready"
	assert captured == {"start_ms": 1000, "end_ms": 4000}
	assert not work_dir.exists()


def test_selected_sound_mixes_with_original_audio(monkeypatch, tmp_path):
	captured = {}
	input_path = tmp_path / "input.mp4"
	sound_path = tmp_path / "sound.mp3"
	output_path = tmp_path / "output.mp4"
	input_path.write_bytes(b"video")
	sound_path.write_bytes(b"sound")
	monkeypatch.setattr(worker, "_has_audio", lambda _path: True)
	monkeypatch.setattr(worker, "_run", lambda cmd, _message, **_kwargs: captured.update(cmd=cmd))
	worker._generate_mp4_with_sound(
		str(input_path), str(sound_path), str(output_path), 12.0,
		{"start_ms": 1000, "duration_ms": 8000, "volume": 0.6},
	)
	filter_graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
	assert "[0:a]" in filter_graph
	assert "[1:a]volume=0.6" in filter_graph
	assert "amix=inputs=2" in filter_graph
