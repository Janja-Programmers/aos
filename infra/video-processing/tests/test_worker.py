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
	)


def payload():
	return {
		"job_id": "job-1",
		"short_id": "SHORT-1",
		"callback_url": "https://callback.invalid/video",
		"raw_video": {"bucket": "raw", "object_key": "clip.mp4"},
	}


def configure_boundaries(monkeypatch, tmp_path):
	work_dir = tmp_path / "worker"
	work_dir.mkdir()
	callbacks = []
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
	monkeypatch.setattr(worker, "_callback", lambda url, result: callbacks.append((url, result)))
	return work_dir, callbacks, uploads


def test_worker_happy_path_stubs_storage_subprocess_and_callback(monkeypatch, tmp_path):
	work_dir, callbacks, uploads = configure_boundaries(monkeypatch, tmp_path)
	result = worker.process_video_job(payload())
	assert result == {"ok": True, "job_id": "job-1", "short_id": "SHORT-1"}
	assert callbacks[0][1]["status"] == "ready"
	assert callbacks[0][1]["duration_seconds"] == 2.5
	assert len(uploads) >= 3
	assert not work_dir.exists()


def test_worker_failure_reports_failure_and_cleans_tempdir(monkeypatch, tmp_path):
	work_dir, callbacks, _uploads = configure_boundaries(monkeypatch, tmp_path)
	monkeypatch.setattr(
		worker,
		"_download_object",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage failed")),
	)
	with pytest.raises(RuntimeError, match="storage failed"):
		worker.process_video_job(payload())
	assert callbacks[-1][1]["status"] == "failed"
	assert not work_dir.exists()
