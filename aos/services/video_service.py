from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid

import frappe
import requests

from aos.api.shorts.constants import MAX_SHORT_DURATION_SECONDS
from aos.services.minio_service import MinioService
from aos.services.media.media_service import MediaService


class VideoService:
    REQUEST_TIMEOUT_SECONDS = 300
    FFMPEG_TIMEOUT_SECONDS = 1800

    @classmethod
    def process_short(cls, short_id: str, force: bool = False):
        """Process or reprocess a short.

        Normal upload processing moves the short through status=processing and then
        status=ready. Forced reprocessing is used after a sound is changed on an
        already-ready short. In that case the short remains ready/visible while the
        new audio/HLS/final MP4 are generated in the background, then URLs are
        swapped only after processing succeeds.
        """
        tmp_input_path = None
        tmp_sound_path = None
        work_dir = None
        is_ready_reprocess = False

        try:
            frappe.logger().info(f"[Shorts] Processing started: {short_id}; force={force}")

            doc = frappe.get_doc("AOS Short", short_id)
            is_ready_reprocess = bool(force and doc.status == "ready")

            if not is_ready_reprocess and doc.status not in ("uploaded", "failed"):
                frappe.logger().info(
                    f"[Shorts] Skipping {short_id}; status={doc.status}; force={force}"
                )
                return

            if not doc.file_key:
                raise Exception("Short file key is missing")

            if is_ready_reprocess:
                if hasattr(doc, "audio_mix_status"):
                    doc.audio_mix_status = "processing"
                if hasattr(doc, "audio_mix_error"):
                    doc.audio_mix_error = None
            else:
                doc.status = "processing"
                doc.processing_error = None
                if hasattr(doc, "audio_mix_status"):
                    doc.audio_mix_status = "processing"
                if hasattr(doc, "audio_mix_error"):
                    doc.audio_mix_error = None

            doc.save(ignore_permissions=True)
            frappe.db.commit()

            minio = MinioService()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_input:
                tmp_input_path = tmp_input.name

            cls._download_source_video(doc, minio, tmp_input_path)
            if not os.path.exists(tmp_input_path) or os.path.getsize(tmp_input_path) == 0:
                raise Exception("Downloaded file is empty")

            work_dir = tempfile.mkdtemp(prefix="aos_short_")

            duration = cls._get_duration(tmp_input_path)
            if duration is None or duration <= 0:
                raise Exception("Invalid video duration")

            if duration > MAX_SHORT_DURATION_SECONDS:
                raise Exception(f"Short must be <= {MAX_SHORT_DURATION_SECONDS} seconds")

            # Generate thumbnail from the original upload. This keeps thumbnail
            # generation independent of audio processing.
            thumbnail_path = os.path.join(work_dir, "thumbnail.jpg")
            cls._generate_thumbnail(tmp_input_path, thumbnail_path)

            sound_config = cls._get_short_sound_config(short_id)
            if sound_config:
                tmp_sound_path = cls._download_sound_file(sound_config, minio)

            final_video_path = os.path.join(work_dir, "final.mp4")
            cls._generate_final_mp4(
                input_file=tmp_input_path,
                output_path=final_video_path,
                duration_seconds=duration,
                sound_file=tmp_sound_path,
                sound_config=sound_config,
            )

            if not os.path.exists(final_video_path) or os.path.getsize(final_video_path) == 0:
                raise Exception("Final video was not generated")

            # HLS is generated from final.mp4 so playback reflects selected music.
            cls._generate_hls(
                input_file=final_video_path,
                work_dir=work_dir,
                has_audio=True,
            )

            master_playlist_path = os.path.join(work_dir, "master.m3u8")
            if not os.path.exists(master_playlist_path):
                raise Exception("HLS master playlist not generated")

            # Use a versioned output path for every processing run.
            # This prevents a ready short reprocess from overwriting the
            # currently playable HLS/final MP4 files before the new version is
            # fully uploaded and committed in the database.
            processing_version = cls._build_processing_version()
            base_key = f"{minio.base_path}/processed/{doc.name}/{processing_version}"
            processed_file_key = f"{base_key}/final.mp4"
            master_playlist_key = f"{base_key}/master.m3u8"
            thumbnail_key = f"{base_key}/thumbnail.jpg"

            for root, _, files in os.walk(work_dir):
                for filename in files:
                    local_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(local_path, work_dir).replace("\\", "/")
                    remote_key = f"{base_key}/{relative_path}"
                    minio.upload_file(remote_key, local_path)

            playback_url = minio.get_public_url(master_playlist_key)
            processed_file_url = minio.get_public_url(processed_file_key)
            thumbnail_url = None
            thumbnail_media = None
            if os.path.exists(thumbnail_path):
                thumbnail_url = minio.get_public_url(thumbnail_key)
                thumbnail_media = cls._create_thumbnail_media(
                    owner_user=doc.owner,
                    short_id=doc.name,
                    thumbnail_path=thumbnail_path,
                )
                if thumbnail_media and getattr(thumbnail_media, "public_url", None):
                    thumbnail_url = thumbnail_media.public_url

            doc.reload()
            doc.playback_url = playback_url
            doc.thumbnail_url = thumbnail_url
            doc.duration_seconds = duration
            if thumbnail_media and doc.meta.has_field("thumbnail_media"):
                doc.thumbnail_media = thumbnail_media.name
            doc.status = "ready"
            doc.processing_error = None

            if hasattr(doc, "processed_file_key"):
                doc.processed_file_key = processed_file_key
            if hasattr(doc, "processed_file_url"):
                doc.processed_file_url = processed_file_url
            if hasattr(doc, "audio_mix_status"):
                doc.audio_mix_status = "ready" if sound_config else "none"
            if hasattr(doc, "audio_mix_error"):
                doc.audio_mix_error = None

            doc.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.logger().info(f"[Shorts] Completed: {short_id}")

        except Exception as exc:
            error_text = str(exc) or "Video processing failed"

            frappe.log_error(
                frappe.get_traceback(),
                f"Video processing failed: {short_id}",
            )

            try:
                failed_doc = frappe.get_doc("AOS Short", short_id)

                if is_ready_reprocess:
                    # Keep the previous playable version alive.
                    if hasattr(failed_doc, "audio_mix_status"):
                        failed_doc.audio_mix_status = "failed"
                    if hasattr(failed_doc, "audio_mix_error"):
                        failed_doc.audio_mix_error = error_text
                else:
                    failed_doc.status = "failed"
                    failed_doc.processing_error = error_text
                    if hasattr(failed_doc, "audio_mix_status"):
                        failed_doc.audio_mix_status = "failed"
                    if hasattr(failed_doc, "audio_mix_error"):
                        failed_doc.audio_mix_error = error_text

                failed_doc.save(ignore_permissions=True)
                frappe.db.commit()

            except Exception:
                frappe.log_error(frappe.get_traceback(), "Failed to update failed state")

        finally:
            cls._cleanup(tmp_input_path, work_dir, tmp_sound_path=tmp_sound_path)

    # PROCESSING VERSION
    @staticmethod
    def _build_processing_version() -> str:
        """Return a unique folder name for one processed output version."""
        return uuid.uuid4().hex

    # DOWNLOAD
    @classmethod
    def _download_file(cls, url: str, destination_path: str):
        with requests.get(url, stream=True, timeout=cls.REQUEST_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        out.write(chunk)

    @classmethod
    def _download_source_video(cls, doc, minio: MinioService, destination_path: str) -> None:
        """Download the raw uploaded short from the new media object or legacy key."""
        raw_media_id = getattr(doc, "raw_video_media", None)
        if raw_media_id:
            media_service = MediaService()
            media_doc = media_service.get_media_doc(raw_media_id)
            payload = media_service.storage.get_bytes(media_doc.bucket, media_doc.object_key)
            with open(destination_path, "wb") as out:
                out.write(payload)
            return

        input_url = minio.get_public_url(doc.file_key)
        if not input_url:
            raise Exception("Could not resolve source video URL")
        cls._download_file(input_url, destination_path)

    @classmethod
    def _create_thumbnail_media(cls, *, owner_user: str, short_id: str, thumbnail_path: str):
        """Create and attach an AOS Media Object for the generated public thumbnail."""
        try:
            with open(thumbnail_path, "rb") as f:
                data = f.read()
            if not data:
                return None

            media_service = MediaService()
            media_doc = media_service.create_uploaded_from_bytes(
                user=owner_user,
                purpose="short_thumbnail",
                filename=f"{short_id}_thumbnail.jpg",
                content_type="image/jpeg",
                data=data,
            )
            media_service.attach_media(
                media_id=media_doc.name,
                user=owner_user,
                purpose="short_thumbnail",
                attached_doctype="AOS Short",
                attached_name=short_id,
                attached_field="thumbnail_media",
            )
            return media_service.get_media_doc(media_doc.name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Short thumbnail media creation failed: {short_id}",
            )
            return None

    # SOUND CONFIG
    @staticmethod
    def _get_short_sound_config(short_id: str) -> dict | None:
        rows = frappe.db.sql(
            """
            SELECT
                ss.sound,
                ss.start_ms,
                ss.duration_ms,
                ss.volume,
                ss.is_original_audio,
                snd.file_key,
                snd.sound_media,
                snd.file_url,
                snd.status
            FROM `tabAOS Short Sound` ss
            INNER JOIN `tabAOS Sound` snd ON snd.name = ss.sound
            WHERE ss.short = %s
            LIMIT 1
            """,
            (short_id,),
            as_dict=True,
        )

        if not rows:
            return None

        sound = rows[0]
        if sound.get("status") != "active":
            raise Exception("Selected sound is not active")

        if int(sound.get("is_original_audio") or 0):
            # Original audio means keep the uploaded video's audio for Phase 4B.
            return None

        if not sound.get("sound_media") and not sound.get("file_key") and not sound.get("file_url"):
            raise Exception("Selected sound file is missing")

        return sound

    @classmethod
    def _download_sound_file(cls, sound_config: dict, minio: MinioService) -> str:
        suffix = ".mp3"
        media_id = sound_config.get("sound_media")
        if media_id:
            media_service = MediaService()
            media_doc = media_service.get_media_doc(media_id)
            _, ext = os.path.splitext(str(media_doc.object_key or media_doc.original_filename or ""))
            if ext:
                suffix = ext
            payload = media_service.storage.get_bytes(media_doc.bucket, media_doc.object_key)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_sound:
                tmp_sound.write(payload)
                tmp_sound_path = tmp_sound.name
        else:
            file_key = sound_config.get("file_key")
            if file_key:
                _, ext = os.path.splitext(str(file_key))
                if ext:
                    suffix = ext
                url = minio.get_public_url(file_key)
            else:
                url = sound_config.get("file_url")
                _, ext = os.path.splitext(str(url or ""))
                if ext:
                    suffix = ext.split("?")[0]

            if not url:
                raise Exception("Could not resolve sound URL")

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_sound:
                tmp_sound_path = tmp_sound.name

            cls._download_file(url, tmp_sound_path)

        if not os.path.exists(tmp_sound_path) or os.path.getsize(tmp_sound_path) == 0:
            raise Exception("Downloaded sound file is empty")

        return tmp_sound_path

    # DURATION
    @classmethod
    def _get_duration(cls, file_path: str):
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            frappe.logger().error(result.stderr)
            return None

        try:
            data = json.loads(result.stdout or "{}")
            return float(data["format"]["duration"])
        except Exception:
            return None

    # AUDIO CHECK
    @classmethod
    def _has_audio(cls, file_path: str):
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return False

        try:
            data = json.loads(result.stdout or "{}")
            return len(data.get("streams", [])) > 0
        except Exception:
            return False

    # FINAL MP4
    @classmethod
    def _generate_final_mp4(
        cls,
        *,
        input_file: str,
        output_path: str,
        duration_seconds: float,
        sound_file: str | None = None,
        sound_config: dict | None = None,
    ):
        if sound_file and sound_config:
            cls._generate_mp4_with_selected_sound(
                input_file=input_file,
                sound_file=sound_file,
                output_path=output_path,
                duration_seconds=duration_seconds,
                start_ms=sound_config.get("start_ms"),
                duration_ms=sound_config.get("duration_ms"),
                volume=sound_config.get("volume"),
            )
            return

        cls._generate_mp4_with_original_audio(
            input_file=input_file,
            output_path=output_path,
            duration_seconds=duration_seconds,
            has_audio=cls._has_audio(input_file),
        )

    @classmethod
    def _generate_mp4_with_original_audio(
        cls,
        *,
        input_file: str,
        output_path: str,
        duration_seconds: float,
        has_audio: bool,
    ):
        if has_audio:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                input_file,
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
                f"{float(duration_seconds):.3f}",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                input_file,
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
                f"{float(duration_seconds):.3f}",
                output_path,
            ]
        cls._run_ffmpeg(cmd, "Final MP4 generation failed")

    @classmethod
    def _generate_mp4_with_selected_sound(
        cls,
        *,
        input_file: str,
        sound_file: str,
        output_path: str,
        duration_seconds: float,
        start_ms=0,
        duration_ms=0,
        volume=1.0,
    ):
        start_seconds = max(float(start_ms or 0) / 1000.0, 0.0)
        selected_duration = float(duration_ms or 0) / 1000.0
        output_duration = float(duration_seconds)
        trim_duration = selected_duration if selected_duration > 0 else output_duration
        trim_duration = max(trim_duration, 0.1)

        try:
            volume_value = float(volume if volume is not None else 1.0)
        except Exception:
            volume_value = 1.0
        volume_value = min(max(volume_value, 0.0), 1.0)

        # Loop selected audio, trim the chosen segment, pad if it is shorter
        # than the video, and force exact output duration so video is never
        # truncated by a short sound clip.
        audio_filter = (
            f"[1:a]volume={volume_value},"
            f"atrim=0:{trim_duration:.3f},"
            "asetpts=PTS-STARTPTS,"
            "apad[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-stream_loop",
            "-1",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            sound_file,
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
            "-movflags",
            "+faststart",
            "-t",
            f"{output_duration:.3f}",
            output_path,
        ]
        cls._run_ffmpeg(cmd, "Selected sound mix failed")

    @classmethod
    def _run_ffmpeg(cls, cmd: list[str], error_message: str):
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _, stderr = process.communicate(timeout=cls.FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            raise Exception("FFmpeg timeout")

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="ignore") if stderr else ""
            frappe.logger().error(f"[Shorts] FFmpeg failed:\n{stderr_text}")
            raise Exception(error_message)

    # THUMBNAIL
    @classmethod
    def _generate_thumbnail(cls, input_file: str, output_path: str):
        """Generate thumbnail safely."""
        attempts = [
            [
                "ffmpeg",
                "-y",
                "-ss",
                "00:00:01",
                "-i",
                input_file,
                "-frames:v",
                "1",
                "-vf",
                "scale=720:-2",
                "-q:v",
                "2",
                output_path,
            ],
            [
                "ffmpeg",
                "-y",
                "-i",
                input_file,
                "-frames:v",
                "1",
                "-vf",
                "scale=720:-2",
                "-q:v",
                "2",
                output_path,
            ],
        ]

        last_error = ""
        for cmd in attempts:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
            )

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return

            last_error = result.stderr or ""

        frappe.logger().error("[Shorts] Thumbnail generation failed:\n" f"{last_error}")
        raise Exception("Thumbnail generation failed")

    # HLS
    @classmethod
    def _generate_hls(cls, input_file: str, work_dir: str, has_audio: bool):
        if has_audio:
            audio_input = []
            audio_map = ["-map", "0:a"]
        else:
            audio_input = [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
            audio_map = ["-map", "1:a"]

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
        ] + audio_input + [
            "-filter_complex",
            (
                "[0:v]split=3[v1][v2][v3];"
                "[v1]scale=1280:720[v1out];"
                "[v2]scale=854:480[v2out];"
                "[v3]scale=426:240[v3out]"
            ),
            "-map",
            "[v1out]",
            *audio_map,
            "-map",
            "[v2out]",
            *audio_map,
            "-map",
            "[v3out]",
            *audio_map,
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

        cls._run_ffmpeg(cmd, "FFmpeg HLS generation failed")

    # CLEANUP
    @staticmethod
    def _cleanup(
        tmp_input_path: str | None,
        work_dir: str | None,
        *,
        tmp_sound_path: str | None = None,
    ):
        for file_path in (tmp_input_path, tmp_sound_path):
            try:
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

        try:
            if work_dir and os.path.exists(work_dir):
                shutil.rmtree(work_dir)
        except Exception:
            pass
