from __future__ import annotations

import json
import time
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import requests
from minio import Minio
from PIL import Image

from app.config import get_settings
from app.security import build_signature


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


def _download_object(client: Minio, *, bucket: str, object_key: str, destination: str) -> None:
    response = None
    try:
        response = client.get_object(bucket, object_key.strip("/"))
        with open(destination, "wb") as out:
            for chunk in response.stream(1024 * 1024):
                if chunk:
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


def _run(cmd: list[str], error_message: str, *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    # Commands are internal argv lists and never use a shell.
    result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        raise VideoProcessingError(f"{error_message}: {stderr or stdout or 'unknown error'}")
    return result


def _duration(path: str) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ],
        "Duration probe failed",
        timeout=120,
    )
    data = json.loads(result.stdout or "{}")
    value = float(data.get("format", {}).get("duration") or 0)
    if value <= 0:
        raise VideoProcessingError("Invalid video duration")
    return value


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
        timeout=120,
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
    raise VideoProcessingError(f"Thumbnail generation failed: {last_error}")


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


def _generate_mp4_with_sound(input_path: str, sound_path: str, output_path: str, duration: float, sound: dict[str, Any]) -> None:
    start_seconds = max(float(sound.get("start_ms") or 0) / 1000.0, 0.0)
    selected_duration = float(sound.get("duration_ms") or 0) / 1000.0
    trim_duration = selected_duration if selected_duration > 0 else float(duration)
    trim_duration = max(trim_duration, 0.1)
    try:
        volume = float(sound.get("volume") if sound.get("volume") is not None else 1.0)
    except Exception:
        volume = 1.0
    volume = min(max(volume, 0.0), 1.0)
    audio_filter = (
        f"[1:a]volume={volume},"
        f"atrim=0:{trim_duration:.3f},"
        "asetpts=PTS-STARTPTS,"
        "apad[aout]"
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


def _callback(callback_url: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signed_payload = timestamp.encode("utf-8") + b"." + body
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Callback-Timestamp": timestamp,
        "X-AOS-Callback-Signature": build_signature(settings.callback_secret, signed_payload),
    }
    response = requests.post(callback_url, data=body, headers=headers, timeout=60)
    response.raise_for_status()


def _failure_payload(payload: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "job_id": payload.get("job_id"),
        "short_id": payload.get("short_id"),
        "status": "failed",
        "error": str(error or "Video processing failed"),
    }


def process_video_job(payload: dict[str, Any]) -> dict[str, Any]:
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

        input_ext = Path(str(raw_video.get("object_key") or "video.mp4")).suffix or ".mp4"
        input_path = os.path.join(work_dir, f"input{input_ext}")
        _download_object(
            client,
            bucket=str(raw_video.get("bucket") or ""),
            object_key=str(raw_video.get("object_key") or ""),
            destination=input_path,
        )

        duration = _duration(input_path)
        max_duration = int(output.get("max_duration_seconds") or settings.max_duration_seconds)
        if duration > max_duration:
            raise VideoProcessingError(f"Short must be <= {max_duration} seconds")

        thumbnail_path = os.path.join(work_dir, "thumbnail.jpg")
        width, height = _generate_thumbnail(input_path, thumbnail_path)

        sound_path = None
        if isinstance(sound, dict) and sound.get("bucket") and sound.get("object_key"):
            sound_ext = Path(str(sound.get("object_key") or "sound.mp3")).suffix or ".mp3"
            sound_path = os.path.join(work_dir, f"sound{sound_ext}")
            _download_object(
                client,
                bucket=str(sound.get("bucket")),
                object_key=str(sound.get("object_key")),
                destination=sound_path,
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
                if filename.startswith("input") or filename.startswith("sound") or filename == "thumbnail.jpg":
                    continue
                local_path = os.path.join(root, filename)
                relative = os.path.relpath(local_path, work_dir).replace("\\", "/")
                remote_key = f"{processed_base_key}/{relative}"
                content_type = "application/vnd.apple.mpegurl" if relative.endswith(".m3u8") else "video/mp2t" if relative.endswith(".ts") else "video/mp4"
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
        }
        _callback(callback_url, callback_payload)
        return {"ok": True, "job_id": job_id, "short_id": short_id}
    except Exception as exc:
        try:
            callback_url = str(payload.get("callback_url") or "").strip()
            if callback_url:
                failure_payload = _failure_payload(payload, str(exc))
                failure_payload["force"] = bool(payload.get("force"))
                _callback(callback_url, failure_payload)
        finally:
            raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
