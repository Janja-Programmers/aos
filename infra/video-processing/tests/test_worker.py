from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from app import worker


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
	monkeypatch.setattr(worker, "_duration", lambda _path: 2.5)
	monkeypatch.setattr(
		worker,
		"_download_object",
		lambda _client, *, destination, **_kwargs: Path(destination).write_bytes(b"video"),
	)
	monkeypatch.setattr(
		worker,
		"_generate_thumbnail",
		lambda _source, destination: Path(destination).write_bytes(b"jpeg") and (320, 480),
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
			uploads.append(kwargs) or {"bucket": kwargs["bucket"], "object_key": kwargs["object_key"]}
		),
	)
	return work_dir, uploads


def test_work_happy_path_is_separate_from_callback(monkeypatch, tmp_path):
	work_dir, uploads = configure_boundaries(monkeypatch, tmp_path)
	result = worker._perform_video_work(payload())
	assert result["job_id"] == "job-1"
	assert result["status"] == "ready"
	assert result["duration_seconds"] == 2.5
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
