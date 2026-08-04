from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import requests
from minio import Minio
from PIL import Image

from app.config import get_settings
from app.durable_lifecycle import deliver_callback, execute_work_job
from app.queue import get_queue, get_redis
from app.security import build_signature

logger = logging.getLogger(__name__)


class VideoProcessingError(Exception):
	pass


def _minio_client() -> Minio:
	settings = get_settings()
	if not settings.minio_access_key or not settings.minio_secret_key:
		raise VideoProcessingError("MinIO credentials are missing")
	return Minio(
		endpoint=settings.minio_endpoint,
		access_key=settings.minio_access_key,
		secret_key=settings.minio_secret_key,
		secure=settings.minio_secure,
	)


def _public_url(bucket: str, object_key: str) -> str:
	settings = get_settings()
	base = settings.minio_public_base_url.rstrip("/")
	return f"{base}/{bucket.strip('/')}/{object_key.strip('/')}" if base else ""


def _output_object_name(file_key: str, output_base_prefix: str = "shorts") -> str:
	key = str(file_key or "").strip().strip("/")
	prefix = str(output_base_prefix or "").strip().strip("/")
	if prefix and key.startswith(f"{prefix}/"):
		return key[len(prefix) + 1 :]
	return key


def _download_object(client: Minio, *, bucket: str, object_key: str, destination: str, max_bytes: int) -> None:
	response = None
	try:
		response = client.get_object(bucket, object_key.strip("/"))
		written = 0
		with open(destination, "wb") as out:
			for chunk in response.stream(1024 * 1024):
				if not chunk:
					continue
				written += len(chunk)
				if written > max_bytes:
					raise VideoProcessingError("Downloaded object exceeds the configured size limit")
				out.write(chunk)
	finally:
		if response is not None:
			response.close()
			response.release_conn()

	if not os.path.exists(destination) or os.path.getsize(destination) <= 0:
		raise VideoProcessingError("Downloaded object is empty")


def _upload_file(
	client: Minio,
	*,
	bucket: str,
	object_key: str,
	file_path: str,
	content_type: str,
	strip_output_prefix: bool = False,
) -> dict[str, Any]:
	target_key = object_key.strip("/")
	if strip_output_prefix:
		target_key = _output_object_name(target_key)

	client.fput_object(
		bucket_name=bucket.strip("/"),
		object_name=target_key,
		file_path=file_path,
		content_type=content_type or "application/octet-stream",
	)
	stat = client.stat_object(bucket.strip("/"), target_key)
	return {
		"bucket": bucket.strip("/"),
		"object_key": object_key.strip("/"),
		"stored_object_key": target_key,
		"size_bytes": int(getattr(stat, "size", 0) or 0),
		"etag": getattr(stat, "etag", "") or "",
		"content_type": content_type,
		"url": _public_url(bucket, target_key),
	}


def _run(cmd: list[str], error_message: str, *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
	# Commands are internal argv lists and never use a shell.
	settings = get_settings()
	if cmd and cmd[0] == "ffmpeg" and "-threads" not in cmd:
		cmd = [*cmd[:-1], "-threads", str(settings.ffmpeg_threads), cmd[-1]]
	result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
		cmd,
		capture_output=True,
		text=True,
		timeout=timeout or settings.ffmpeg_timeout_seconds,
	)
	if result.returncode != 0:
		# Keep companion errors bounded and avoid propagating media metadata or paths.
		raise VideoProcessingError(error_message)
	return result


def _probe_video(path: str) -> dict[str, Any]:
	settings = get_settings()
	result = _run(
		[
			"ffprobe", "-v", "error", "-select_streams", "v:0",
			"-show_entries", "stream=codec_name,width,height:format=duration",
			"-of", "json", path,
		],
		"Video metadata probe failed",
		timeout=settings.ffprobe_timeout_seconds,
	)
	try:
		data = json.loads(result.stdout or "{}")
		stream = (data.get("streams") or [])[0]
		duration = float((data.get("format") or {}).get("duration") or 0)
		width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
		codec = str(stream.get("codec_name") or "").lower()
	except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
		raise VideoProcessingError("Invalid video metadata") from exc
	if duration <= 0 or width <= 0 or height <= 0:
		raise VideoProcessingError("Invalid video metadata")
	if codec not in settings.allowed_video_codecs:
		raise VideoProcessingError("Unsupported video codec")
	if width > settings.max_width or height > settings.max_height or width * height > settings.max_pixels:
		raise VideoProcessingError("Video dimensions exceed configured limits")
	ratio = width / height
	if ratio < settings.min_aspect_ratio or ratio > settings.max_aspect_ratio:
		raise VideoProcessingError("Video aspect ratio exceeds configured limits")
	return {"duration": duration, "width": width, "height": height, "codec": codec}


def _duration(path: str) -> float:
	return float(_probe_video(path)["duration"])


def _has_audio(path: str) -> bool:
	result = subprocess.run(
		[
			"ffprobe",
			"-v",
			"error",
			"-select_streams",
			"a",
			"-show_entries",
			"stream=index",
			"-of",
			"json",
			path,
		],
		capture_output=True,
		text=True,
		timeout=get_settings().ffprobe_timeout_seconds,
	)
	if result.returncode != 0:
		return False
	try:
		data = json.loads(result.stdout or "{}")
		return bool(data.get("streams"))
	except Exception:
		return False


def _generate_thumbnail(input_path: str, output_path: str) -> tuple[int | None, int | None]:
	last_error = ""
	candidates = ["00:00:01", "00:00:00.2", "00:00:00"]
	for timestamp in candidates:
		result = subprocess.run(
			[
				"ffmpeg",
				"-y",
				"-ss",
				timestamp,
				"-i",
				input_path,
				"-frames:v",
				"1",
				"-q:v",
				"2",
				output_path,
			],
			capture_output=True,
			text=True,
			timeout=180,
		)
		if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
			try:
				with Image.open(output_path) as img:
					return int(img.width), int(img.height)
			except Exception:
				return None, None
		last_error = result.stderr or result.stdout or last_error
	raise VideoProcessingError("Thumbnail generation failed")


def _generate_mp4_original(input_path: str, output_path: str, duration: float) -> None:
	if _has_audio(input_path):
		cmd = [
			"ffmpeg",
			"-y",
			"-i",
			input_path,
			"-map",
			"0:v:0",
			"-map",
			"0:a:0?",
			"-c:v",
			"libx264",
			"-preset",
			"veryfast",
			"-c:a",
			"aac",
			"-movflags",
			"+faststart",
			"-t",
			f"{duration:.3f}",
			output_path,
		]
	else:
		cmd = [
			"ffmpeg",
			"-y",
			"-i",
			input_path,
			"-f",
			"lavfi",
			"-i",
			"anullsrc=channel_layout=stereo:sample_rate=44100",
			"-map",
			"0:v:0",
			"-map",
			"1:a:0",
			"-c:v",
			"libx264",
			"-preset",
			"veryfast",
			"-c:a",
			"aac",
			"-movflags",
			"+faststart",
			"-t",
			f"{duration:.3f}",
			output_path,
		]
	_run(cmd, "Final MP4 generation failed")


def _generate_mp4_with_sound(
	input_path: str, sound_path: str, output_path: str, duration: float, sound: dict[str, Any]
) -> None:
	start_seconds = max(float(sound.get("start_ms") or 0) / 1000.0, 0.0)
	selected_duration = float(sound.get("duration_ms") or 0) / 1000.0
	trim_duration = selected_duration if selected_duration > 0 else float(duration)
	trim_duration = max(trim_duration, 0.1)
	try:
		volume = float(sound.get("volume") if sound.get("volume") is not None else 1.0)
	except Exception:
		volume = 1.0
	volume = min(max(volume, 0.0), 1.0)
	audio_filter = f"[1:a]volume={volume},atrim=0:{trim_duration:.3f},asetpts=PTS-STARTPTS,apad[aout]"
	cmd = [
		"ffmpeg",
		"-y",
		"-i",
		input_path,
		"-stream_loop",
		"-1",
		"-ss",
		f"{start_seconds:.3f}",
		"-i",
		sound_path,
		"-filter_complex",
		audio_filter,
		"-map",
		"0:v:0",
		"-map",
		"[aout]",
		"-c:v",
		"libx264",
		"-preset",
		"veryfast",
		"-c:a",
		"aac",
		"-shortest",
		"-movflags",
		"+faststart",
		"-t",
		f"{float(duration):.3f}",
		output_path,
	]
	_run(cmd, "Final MP4 with selected sound failed")


def _generate_hls(input_path: str, work_dir: str) -> None:
	cmd = [
		"ffmpeg",
		"-y",
		"-i",
		input_path,
		"-filter_complex",
		"[0:v]split=3[v1][v2][v3];[v1]scale=1280:720[v1out];[v2]scale=854:480[v2out];[v3]scale=426:240[v3out]",
		"-map",
		"[v1out]",
		"-map",
		"0:a",
		"-map",
		"[v2out]",
		"-map",
		"0:a",
		"-map",
		"[v3out]",
		"-map",
		"0:a",
		"-c:v",
		"libx264",
		"-c:a",
		"aac",
		"-preset",
		"veryfast",
		"-b:v:0",
		"3000k",
		"-b:v:1",
		"1500k",
		"-b:v:2",
		"600k",
		"-shortest",
		"-f",
		"hls",
		"-hls_time",
		"4",
		"-hls_playlist_type",
		"vod",
		"-hls_segment_filename",
		os.path.join(work_dir, "v%v_seg_%03d.ts"),
		"-master_pl_name",
		"master.m3u8",
		"-var_stream_map",
		"v:0,a:0 v:1,a:1 v:2,a:2",
		os.path.join(work_dir, "v%v.m3u8"),
	]
	_run(cmd, "HLS generation failed")


def _generate_classification_frames(
	input_path: str,
	work_dir: str,
	duration: float,
	thumbnail_path: str,
) -> list[str]:
	settings = get_settings()
	paths = [thumbnail_path] if os.path.exists(thumbnail_path) else []
	remaining = max(0, settings.classification_frame_count - len(paths))
	if remaining <= 0:
		return paths

	# Evenly spread samples avoid classifying only an intro/title frame.
	for index in range(remaining):
		fraction = (index + 1) / (remaining + 1)
		timestamp = max(0.0, min(float(duration) - 0.05, float(duration) * fraction))
		path = os.path.join(work_dir, f"classification_{index:02d}.jpg")
		try:
			_run(
				[
					"ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", input_path,
					"-frames:v", "1",
					"-vf", "scale=640:-2:force_original_aspect_ratio=decrease",
					"-q:v", "5", path,
				],
				"Classification frame extraction failed",
				timeout=min(settings.ffmpeg_timeout_seconds, 180),
			)
			if os.path.exists(path) and 0 < os.path.getsize(path) <= settings.classification_max_frame_bytes:
				paths.append(path)
		except Exception:
			logger.warning("Short classification frame extraction failed category=processing")
	return paths[: settings.classification_frame_count]


def _validate_classification_url(url: str) -> None:
	settings = get_settings()
	parsed = urlparse(url)
	if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
		raise VideoProcessingError("Invalid classification service URL")
	if settings.classification_allowed_hosts and parsed.hostname.lower() not in settings.classification_allowed_hosts:
		raise VideoProcessingError("Invalid classification service host")


def _classification_fallback(status: str = "unavailable") -> dict[str, Any]:
	return {
		"status": status,
		"mode": "vibes",
		"confidence": 0.0,
		"scores": {"shop": 0.0, "geo": 0.0, "vibes": 0.0, "learn": 0.0},
		"model": "fallback",
		"model_version": "1",
		"frame_count": 0,
	}


def _classify_frames(frame_paths: list[str]) -> dict[str, Any]:
	settings = get_settings()
	if not settings.classification_enabled or not frame_paths:
		return _classification_fallback("disabled" if not settings.classification_enabled else "unavailable")
	if not settings.classification_secret:
		return _classification_fallback("unavailable")

	try:
		_validate_classification_url(settings.classification_url)
		frames: list[str] = []
		for path in frame_paths[: settings.classification_frame_count]:
			if not os.path.exists(path):
				continue
			size = os.path.getsize(path)
			if size <= 0 or size > settings.classification_max_frame_bytes:
				continue
			with open(path, "rb") as handle:
				frames.append(base64.b64encode(handle.read()).decode("ascii"))
		if not frames:
			return _classification_fallback("unavailable")

		body = json.dumps({"frames": frames}, separators=(",", ":"), sort_keys=True).encode("utf-8")
		timestamp = str(int(time.time()))
		signed_payload = timestamp.encode("ascii") + b"." + body
		response = requests.post(
			settings.classification_url,
			data=body,
			headers={
				"Content-Type": "application/json",
				"X-AOS-Timestamp": timestamp,
				"X-AOS-Signature": build_signature(settings.classification_secret, signed_payload),
			},
			timeout=settings.classification_timeout_seconds,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
		scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
		clean_scores = {}
		for mode in ("shop", "geo", "vibes", "learn"):
			try:
				clean_scores[mode] = max(0.0, min(float(scores.get(mode) or 0.0), 1.0))
			except (TypeError, ValueError):
				clean_scores[mode] = 0.0
		if sum(clean_scores.values()) <= 0:
			return _classification_fallback("unavailable")
		mode = str(data.get("mode") or "").strip().lower()
		if mode not in clean_scores:
			mode = max(clean_scores, key=clean_scores.get)
		return {
			"status": "ready",
			"mode": mode,
			"confidence": max(0.0, min(float(data.get("confidence") or clean_scores[mode]), 1.0)),
			"scores": clean_scores,
			"model": str(data.get("model") or "openclip")[:140],
			"model_version": str(data.get("model_version") or "1")[:140],
			"frame_count": len(frames),
		}
	except Exception as exc:
		logger.warning("Short visual classification unavailable category=%s", exc.__class__.__name__)
		return _classification_fallback("unavailable")


def _validate_callback_url(callback_url: str) -> None:
	settings = get_settings()
	parsed = urlparse(callback_url)
	if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
		raise VideoProcessingError("Invalid callback URL")
	if settings.environment.lower() in {"production", "staging"}:
		if parsed.scheme != "https" or not settings.callback_allowed_hosts:
			raise VideoProcessingError("Invalid callback URL")
	if settings.callback_allowed_hosts and parsed.hostname.lower() not in settings.callback_allowed_hosts:
		raise VideoProcessingError("Invalid callback host")


def _callback(callback_url: str, payload: dict[str, Any]) -> Any:
	settings = get_settings()
	_validate_callback_url(callback_url)
	body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
	timestamp = str(int(time.time()))
	signed_payload = timestamp.encode("utf-8") + b"." + body
	headers = {
		"Content-Type": "application/json",
		"X-AOS-Callback-Timestamp": timestamp,
		"X-AOS-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
	}
	response = requests.post(callback_url, data=body, headers=headers, timeout=settings.callback_timeout_seconds)
	return response


def _failure_payload(payload: dict[str, Any], error: str) -> dict[str, Any]:
	return {
		"job_id": payload.get("job_id"),
		"idempotency_key": payload.get("idempotency_key"),
		"dispatch_id": payload.get("dispatch_id"),
		"dispatch_generation": payload.get("dispatch_generation"),
		"dispatch_token": payload.get("dispatch_token"),
		"job_generation": payload.get("job_generation"),
		"short_id": payload.get("short_id"),
		"status": "failed",
		"error": "VIDEO_PROCESSING_FAILED",
	}


def _perform_video_work(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	client = _minio_client()
	work_dir = tempfile.mkdtemp(prefix="aos_video_")
	try:
		short_id = str(payload.get("short_id") or "").strip()
		job_id = str(payload.get("job_id") or "").strip()
		callback_url = str(payload.get("callback_url") or "").strip()
		raw_video = dict(payload.get("raw_video") or {})
		sound = payload.get("sound")
		output = dict(payload.get("output") or {})

		if not short_id or not job_id or not callback_url:
			raise VideoProcessingError("Invalid video job payload")
		_validate_callback_url(callback_url)
		if not short_id.startswith("SHORT-") or ".." in str(raw_video.get("object_key") or "").split("/"):
			raise VideoProcessingError("Invalid video job payload")
		expected_size = max(0, int(raw_video.get("size_bytes") or 0))
		if expected_size > settings.max_input_bytes:
			raise VideoProcessingError("Input video exceeds configured size limit")

		input_ext = Path(str(raw_video.get("object_key") or "video.mp4")).suffix or ".mp4"
		input_path = os.path.join(work_dir, f"input{input_ext}")
		_download_object(
			client,
			bucket=str(raw_video.get("bucket") or ""),
			object_key=str(raw_video.get("object_key") or ""),
			destination=input_path,
			max_bytes=settings.max_input_bytes,
		)

		metadata = _probe_video(input_path)
		duration = float(metadata["duration"])
		max_duration = int(output.get("max_duration_seconds") or settings.max_duration_seconds)
		if duration > max_duration:
			raise VideoProcessingError(f"Short must be <= {max_duration} seconds")

		thumbnail_path = os.path.join(work_dir, "thumbnail.jpg")
		width, height = _generate_thumbnail(input_path, thumbnail_path)
		classification_frames = _generate_classification_frames(
			input_path, work_dir, duration, thumbnail_path
		)
		classification = _classify_frames(classification_frames)

		sound_path = None
		if isinstance(sound, dict) and sound.get("bucket") and sound.get("object_key"):
			sound_ext = Path(str(sound.get("object_key") or "sound.mp3")).suffix or ".mp3"
			sound_path = os.path.join(work_dir, f"sound{sound_ext}")
			_download_object(
				client,
				bucket=str(sound.get("bucket")),
				object_key=str(sound.get("object_key")),
				destination=sound_path,
				max_bytes=min(settings.max_input_bytes, 134217728),
			)

		final_path = os.path.join(work_dir, "final.mp4")
		if sound_path:
			_generate_mp4_with_sound(input_path, sound_path, final_path, duration, sound or {})
		else:
			_generate_mp4_original(input_path, final_path, duration)

		if not os.path.exists(final_path) or os.path.getsize(final_path) <= 0:
			raise VideoProcessingError("Final MP4 was not generated")

		_generate_hls(final_path, work_dir)
		master_path = os.path.join(work_dir, "master.m3u8")
		if not os.path.exists(master_path):
			raise VideoProcessingError("HLS master playlist was not generated")

		version = uuid.uuid4().hex
		output_bucket = str(output.get("output_bucket") or settings.output_bucket).strip("/")
		output_base = str(output.get("output_base_path") or settings.output_base_path).strip("/")
		if output_base.endswith("/"):
			output_base = output_base[:-1]
		processed_base_key = f"{output_base}/{short_id}/{version}".strip("/")
		processed_file_key = f"{processed_base_key}/final.mp4"
		master_playlist_key = f"{processed_base_key}/master.m3u8"

		uploaded_objects: list[dict[str, Any]] = []
		for root, _, files in os.walk(work_dir):
			for filename in files:
				if (
					filename.startswith("input")
					or filename.startswith("sound")
					or filename.startswith("classification_")
					or filename == "thumbnail.jpg"
				):
					continue
				local_path = os.path.join(root, filename)
				relative = os.path.relpath(local_path, work_dir).replace("\\", "/")
				remote_key = f"{processed_base_key}/{relative}"
				content_type = (
					"application/vnd.apple.mpegurl"
					if relative.endswith(".m3u8")
					else "video/mp2t"
					if relative.endswith(".ts")
					else "video/mp4"
				)
				uploaded_objects.append(
					_upload_file(
						client,
						bucket=output_bucket,
						object_key=remote_key,
						file_path=local_path,
						content_type=content_type,
						strip_output_prefix=True,
					)
				)

		thumbnail_bucket = str(output.get("thumbnail_bucket") or settings.thumbnail_bucket).strip("/")
		thumbnail_base = str(output.get("thumbnail_base_path") or settings.thumbnail_base_path).strip("/")
		thumbnail_key = f"{thumbnail_base}/{short_id}/{version}/thumbnail.jpg"
		thumbnail = _upload_file(
			client,
			bucket=thumbnail_bucket,
			object_key=thumbnail_key,
			file_path=thumbnail_path,
			content_type="image/jpeg",
			strip_output_prefix=False,
		)
		thumbnail.update({"width": width, "height": height, "filename": f"{short_id}_thumbnail.jpg"})

		callback_payload = {
			"job_id": job_id,
			"idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"),
			"dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"),
			"job_generation": payload.get("job_generation"),
			"short_id": short_id,
			"status": "ready",
			"duration_seconds": duration,
			"playback_url": _public_url(output_bucket, _output_object_name(master_playlist_key)),
			"processed_file_url": _public_url(output_bucket, _output_object_name(processed_file_key)),
			"processed_file_key": processed_file_key,
			"master_playlist_key": master_playlist_key,
			"thumbnail": thumbnail,
			"objects": uploaded_objects,
			"force": bool(payload.get("force")),
			"sound_applied": bool(sound_path),
			"classification": classification,
		}
		return callback_payload
	except Exception as exc:
		logger.error("Video processing job failed category=%s", exc.__class__.__name__)
		raise
	finally:
		shutil.rmtree(work_dir, ignore_errors=True)


def process_video_job(payload: dict[str, Any]) -> dict[str, Any]:
	settings = get_settings()
	return execute_work_job(
		redis=get_redis(),
		queue=get_queue(),
		service_type="video_processing",
		payload=payload,
		perform_work=_perform_video_work,
		failure_payload=_failure_payload,
		callback_worker_method="app.worker.deliver_callback_job",
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		failure_ttl_seconds=int(getattr(settings, "failure_ttl_seconds", 604800)),
		work_lock_seconds=int(getattr(settings, "job_timeout_seconds", 600)) + 300,
		callback_timeout_seconds=int(getattr(settings, "callback_job_timeout_seconds", 120)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def deliver_callback_job(stable_id: str) -> dict[str, Any]:
	settings = get_settings()
	return deliver_callback(
		redis=get_redis(),
		service_type="video_processing",
		stable_id=stable_id,
		send_callback=_callback,
		result_ttl_seconds=int(getattr(settings, "durable_result_ttl_seconds", 604800)),
		callback_max_attempts=int(getattr(settings, "callback_max_attempts", 8)),
	)


def replay_callback(_callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Compatibility entry point: replay from the durable result, never from RQ result data."""
	stable_id = str(payload.get("idempotency_key") or payload.get("job_id") or "").strip()
	return deliver_callback_job(stable_id)

