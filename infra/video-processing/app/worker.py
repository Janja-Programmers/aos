from __future__ import annotations

import base64
import json
import logging
import os
import re
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
from app.queue import get_callback_queue, get_queue, get_redis
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
) -> dict[str, Any]:
	target_key = object_key.strip("/")
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
		# Keep caller-facing errors generic, but retain a bounded diagnostic in the
		# companion log so operational failures can be diagnosed without replaying
		# the workload. Local temporary paths are redacted.
		diagnostic = (result.stderr or result.stdout or "").strip()
		diagnostic = re.sub(r"/tmp/aos_video_[^\s'\"]+", "<video-workdir>", diagnostic)
		if len(diagnostic) > 2000:
			diagnostic = diagnostic[-2000:]
		logger.error("%s exit_code=%s diagnostic=%s", error_message, result.returncode, diagnostic or "unavailable")
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


def _extract_original_audio(input_path: str, output_path: str, duration: float) -> None:
	"""Extract the creator's original audio into a reusable AAC/M4A asset."""
	cmd = [
		"ffmpeg",
		"-y",
		"-i",
		input_path,
		"-vn",
		"-map",
		"0:a:0",
		"-c:a",
		"aac",
		"-b:a",
		"192k",
		"-movflags",
		"+faststart",
		"-t",
		f"{float(duration):.3f}",
		output_path,
	]
	_run(cmd, "Original audio extraction failed")


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
	"""Mix selected sound with original video audio when present.

	The selected sound is looped and trimmed to the Short duration. Videos with
	no original audio still receive the selected sound as their only audio track.
	"""
	start_seconds = max(float(sound.get("start_ms") or 0) / 1000.0, 0.0)
	selected_duration = float(sound.get("duration_ms") or 0) / 1000.0
	trim_duration = selected_duration if selected_duration > 0 else float(duration)
	trim_duration = max(trim_duration, 0.1)
	try:
		volume = float(sound.get("volume") if sound.get("volume") is not None else 1.0)
	except Exception:
		volume = 1.0
	volume = min(max(volume, 0.0), 1.0)

	if _has_audio(input_path):
		audio_filter = (
			f"[0:a]aresample=async=1:first_pts=0,apad,"
			f"atrim=0:{float(duration):.3f}[original];"
			f"[1:a]volume={volume},atrim=0:{trim_duration:.3f},"
			f"asetpts=PTS-STARTPTS,apad,atrim=0:{float(duration):.3f}[music];"
			"[original][music]amix=inputs=2:duration=longest:"
			f"dropout_transition=2:normalize=1,atrim=0:{float(duration):.3f}[aout]"
		)
	else:
		audio_filter = (
			f"[1:a]volume={volume},atrim=0:{trim_duration:.3f},"
			f"asetpts=PTS-STARTPTS,apad,atrim=0:{float(duration):.3f}[aout]"
		)

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
	"""Generate three portrait-safe HLS variants plus a master playlist."""
	cmd = [
		"ffmpeg", "-y", "-i", input_path,
		"-filter_complex",
		"[0:v]split=3[v0][v1][v2];"
		"[v0]scale=-2:1280:force_original_aspect_ratio=decrease:force_divisible_by=2[v0o];"
		"[v1]scale=-2:854:force_original_aspect_ratio=decrease:force_divisible_by=2[v1o];"
		"[v2]scale=-2:640:force_original_aspect_ratio=decrease:force_divisible_by=2[v2o]",
		"-map", "[v0o]", "-map", "0:a:0",
		"-map", "[v1o]", "-map", "0:a:0",
		"-map", "[v2o]", "-map", "0:a:0",
		"-c:v", "libx264", "-c:a", "aac", "-preset", "veryfast",
		"-b:v:0", "2800k", "-b:v:1", "1400k", "-b:v:2", "700k",
		"-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
		"-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
		"-hls_segment_filename", os.path.join(work_dir, "v%v_seg_%03d.ts"),
		"-master_pl_name", "master.m3u8",
		"-var_stream_map", "v:0,a:0 v:1,a:1 v:2,a:2",
		os.path.join(work_dir, "v%v.m3u8"),
	]
	_run(cmd, "HLS generation failed")


def _generate_storyboard(input_path: str, image_path: str, manifest_path: str, duration: float) -> tuple[int, int]:
	frame_count = max(4, min(20, int(duration) if duration >= 4 else 4))
	columns = 5
	rows = (frame_count + columns - 1) // columns
	fps = frame_count / max(duration, 0.1)
	filter_graph = f"fps={fps:.6f},scale=180:-2:force_original_aspect_ratio=decrease,tile={columns}x{rows}:padding=0:margin=0"
	_run(["ffmpeg", "-y", "-i", input_path, "-vf", filter_graph, "-frames:v", "1", "-q:v", "5", image_path], "Storyboard generation failed")
	with Image.open(image_path) as image:
		width, height = int(image.width), int(image.height)
	cell_width = max(1, width // columns)
	cell_height = max(1, height // rows)
	manifest = {
		"version": 1, "duration_seconds": round(float(duration), 3), "frame_count": frame_count,
		"columns": columns, "rows": rows, "cell_width": cell_width, "cell_height": cell_height,
		"interval_seconds": round(float(duration) / frame_count, 6),
	}
	Path(manifest_path).write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
	return width, height


def _normalize_vertical(input_path: str, output_path: str, *, duration: float | None = None) -> None:
	video_filter = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"
	if _has_audio(input_path):
		cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", video_filter, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart"]
	else:
		cmd = ["ffmpeg", "-y", "-i", input_path, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100", "-vf", video_filter, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", "-movflags", "+faststart"]
	if duration is not None:
		cmd.extend(["-t", f"{float(duration):.3f}"])
	cmd.append(output_path)
	_run(cmd, "Video normalization failed")


def _compose_side_by_side(source_path: str, creator_path: str, output_path: str, max_duration: float) -> None:
	cmd = [
		"ffmpeg", "-y", "-i", source_path, "-i", creator_path,
		"-filter_complex",
		"[0:v]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:black[left];"
		"[1:v]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:black[right];"
		"[left][right]hstack=inputs=2[v]",
		"-map", "[v]", "-map", "1:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
		"-shortest", "-t", f"{float(max_duration):.3f}", "-movflags", "+faststart", output_path,
	]
	_run(cmd, "Side-by-side composition failed")


def _compose_segment(source_path: str, creator_path: str, output_path: str, start_ms: int, end_ms: int, max_duration: float, work_dir: str) -> None:
	segment_seconds = (end_ms - start_ms) / 1000.0
	if start_ms < 0 or segment_seconds <= 0 or segment_seconds > 60:
		raise VideoProcessingError("Invalid reusable source segment")
	trimmed = os.path.join(work_dir, "source_trimmed.mp4")
	source_norm = os.path.join(work_dir, "source_segment.mp4")
	creator_norm = os.path.join(work_dir, "creator_norm.mp4")
	_run(["ffmpeg", "-y", "-ss", f"{start_ms / 1000.0:.3f}", "-i", source_path, "-t", f"{segment_seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", trimmed], "Source segment extraction failed")
	_normalize_vertical(trimmed, source_norm, duration=segment_seconds)
	_normalize_vertical(creator_path, creator_norm)
	concat_file = os.path.join(work_dir, "concat.txt")
	Path(concat_file).write_text(f"file '{source_norm}'\nfile '{creator_norm}'\n", encoding="utf-8")
	_run(["ffmpeg", "-y", "-f", "concat", "-safe", "1", "-i", concat_file, "-c", "copy", "-t", f"{float(max_duration):.3f}", output_path], "Segment composition failed")


def _generate_watermarked_download(input_path: str, output_path: str, max_duration: float) -> None:
	_run([
		"ffmpeg", "-y", "-i", input_path,
		"-vf", "drawtext=text=AOS:fontcolor=white@0.85:fontsize=32:box=1:boxcolor=black@0.35:boxborderw=10:x=w-tw-24:y=h-th-24",
		"-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart",
		"-t", f"{float(max_duration):.3f}", output_path,
	], "Watermarked download generation failed")


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
		"operation": payload.get("operation"),
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
		operation = str(payload.get("operation") or "Process").strip()
		callback_url = str(payload.get("callback_url") or "").strip()
		raw_video = dict(payload.get("raw_video") or {})
		sound = payload.get("sound")
		output = dict(payload.get("output") or {})
		if not short_id.startswith("SHR-") or not job_id or not callback_url or operation not in {"Process", "Download", "Side By Side", "Segment"}:
			raise VideoProcessingError("Invalid video job payload")
		_validate_callback_url(callback_url)
		if ".." in str(raw_video.get("object_key") or "").split("/") or "\\" in str(raw_video.get("object_key") or ""):
			raise VideoProcessingError("Invalid video job payload")
		if max(0, int(raw_video.get("size_bytes") or 0)) > settings.max_input_bytes:
			raise VideoProcessingError("Input video exceeds configured size limit")

		input_ext = Path(str(raw_video.get("object_key") or "video.mp4")).suffix or ".mp4"
		input_path = os.path.join(work_dir, f"input{input_ext}")
		_download_object(client, bucket=str(raw_video.get("bucket") or ""), object_key=str(raw_video.get("object_key") or ""), destination=input_path, max_bytes=settings.max_input_bytes)
		input_meta = _probe_video(input_path)
		max_duration = int(output.get("max_duration_seconds") or settings.max_duration_seconds)

		version = uuid.uuid4().hex
		outputs: dict[str, Any] = {}
		classification = None

		if operation == "Download":
			download_path = os.path.join(work_dir, "download.mp4")
			_generate_watermarked_download(input_path, download_path, min(float(input_meta["duration"]), float(max_duration)))
			key = f"{str(output['download_base_path']).strip('/')}/{short_id}/{version}/AOS.mp4"
			meta = _upload_file(client, bucket=str(output["download_bucket"]), object_key=key, file_path=download_path, content_type="video/mp4")
			meta.update({"filename": f"{short_id}_AOS.mp4"})
			outputs["download"] = meta
			duration = float(input_meta["duration"])
		else:
			working_source = input_path
			if operation in {"Side By Side", "Segment"}:
				source = dict(payload.get("source_video") or {})
				if not source.get("bucket") or not source.get("object_key"):
					raise VideoProcessingError("Reusable source video is missing")
				source_path = os.path.join(work_dir, "source.mp4")
				_download_object(client, bucket=str(source["bucket"]), object_key=str(source["object_key"]), destination=source_path, max_bytes=settings.max_input_bytes)
				_probe_video(source_path)
				composed = os.path.join(work_dir, "composed.mp4")
				if operation == "Side By Side":
					_compose_side_by_side(source_path, input_path, composed, max_duration)
				else:
					_compose_segment(source_path, input_path, composed, int(payload.get("source_start_ms") or 0), int(payload.get("source_end_ms") or 0), max_duration, work_dir)
				working_source = composed

			working_meta = _probe_video(working_source)
			if float(working_meta["duration"]) > max_duration:
				raise VideoProcessingError(f"Short must be <= {max_duration} seconds")
			duration = min(float(working_meta["duration"]), float(max_duration))

			poster_path = os.path.join(work_dir, "poster.jpg")
			poster_width, poster_height = _generate_thumbnail(working_source, poster_path)
			classification_frames = _generate_classification_frames(working_source, work_dir, duration, poster_path)
			classification = _classify_frames(classification_frames)

			sound_path = None
			if isinstance(sound, dict) and sound.get("bucket") and sound.get("object_key"):
				sound_ext = Path(str(sound.get("object_key") or "sound.m4a")).suffix or ".m4a"
				sound_path = os.path.join(work_dir, f"sound{sound_ext}")
				_download_object(client, bucket=str(sound["bucket"]), object_key=str(sound["object_key"]), destination=sound_path, max_bytes=min(settings.max_input_bytes, 134217728))

			final_path = os.path.join(work_dir, "final.mp4")
			if sound_path:
				_generate_mp4_with_sound(working_source, sound_path, final_path, duration, sound or {})
			else:
				_generate_mp4_original(working_source, final_path, duration)
			final_meta = _probe_video(final_path)
			duration = float(final_meta["duration"])

			playback_base = f"{str(output['playback_base_path']).strip('/')}/{short_id}/{version}"
			playback_meta = _upload_file(client, bucket=str(output["playback_bucket"]), object_key=f"{playback_base}/playback.mp4", file_path=final_path, content_type="video/mp4")
			playback_meta.update({"filename": f"{short_id}.mp4"})
			outputs["playback"] = playback_meta

			hls_dir = os.path.join(work_dir, "hls"); os.makedirs(hls_dir, exist_ok=True)
			_generate_hls(final_path, hls_dir)
			manifest_meta = None
			for filename in sorted(os.listdir(hls_dir)):
				local = os.path.join(hls_dir, filename)
				if not os.path.isfile(local):
					continue
				ctype = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
				item = _upload_file(client, bucket=str(output["playback_bucket"]), object_key=f"{playback_base}/hls/{filename}", file_path=local, content_type=ctype)
				if filename == "master.m3u8":
					manifest_meta = item; manifest_meta.update({"filename": "master.m3u8"})
			if not manifest_meta:
				raise VideoProcessingError("HLS master playlist was not generated")
			outputs["manifest"] = manifest_meta

			poster_key = f"{str(output['poster_base_path']).strip('/')}/{short_id}/{version}/poster.jpg"
			poster_meta = _upload_file(client, bucket=str(output["poster_bucket"]), object_key=poster_key, file_path=poster_path, content_type="image/jpeg")
			poster_meta.update({"filename": f"{short_id}_poster.jpg", "width": poster_width, "height": poster_height})
			outputs["poster"] = poster_meta

			storyboard_path = os.path.join(work_dir, "storyboard.jpg")
			storyboard_manifest_path = os.path.join(work_dir, "storyboard.json")
			sb_width, sb_height = _generate_storyboard(final_path, storyboard_path, storyboard_manifest_path, duration)
			story_base = f"{str(output['storyboard_base_path']).strip('/')}/{short_id}/{version}"
			sb = _upload_file(client, bucket=str(output["storyboard_bucket"]), object_key=f"{story_base}/storyboard.jpg", file_path=storyboard_path, content_type="image/jpeg")
			sb.update({"filename": f"{short_id}_storyboard.jpg", "width": sb_width, "height": sb_height})
			outputs["storyboard"] = sb
			sbm = _upload_file(client, bucket=str(output["storyboard_bucket"]), object_key=f"{story_base}/storyboard.json", file_path=storyboard_manifest_path, content_type="application/json")
			sbm.update({"filename": f"{short_id}_storyboard.json"})
			outputs["storyboard_manifest"] = sbm

			if not sound_path and _has_audio(working_source):
				audio_path = os.path.join(work_dir, "original.m4a")
				_extract_original_audio(final_path, audio_path, duration)
				audio_key = f"{str(output['sound_base_path']).strip('/')}/{short_id}/{version}/original.m4a"
				audio = _upload_file(client, bucket=str(output["sound_bucket"]), object_key=audio_key, file_path=audio_path, content_type="audio/mp4")
				audio.update({"filename": f"{short_id}_original.m4a", "duration_seconds": duration})
				outputs["original_audio"] = audio

		result = {
			"job_id": job_id, "idempotency_key": payload.get("idempotency_key"),
			"dispatch_id": payload.get("dispatch_id"), "dispatch_generation": payload.get("dispatch_generation"),
			"dispatch_token": payload.get("dispatch_token"), "job_generation": payload.get("job_generation"),
			"short_id": short_id, "operation": operation, "status": "ready", "duration_seconds": duration, "outputs": outputs,
		}
		if classification is not None:
			result["classification"] = classification
		return result
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
		callback_queue=get_callback_queue(),
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
