from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import frappe
import requests

from aos.api.shorts.constants import MAX_SHORT_DURATION_SECONDS
from aos.services.minio_service import MinioService


class VideoService:
    REQUEST_TIMEOUT_SECONDS = 300
    FFMPEG_TIMEOUT_SECONDS = 1800

    @classmethod
    def process_short(cls, short_id: str):
        tmp_input_path = None
        work_dir = None

        try:
            frappe.logger().info(f"[Shorts] Processing started: {short_id}")

            doc = frappe.get_doc("AOS Short", short_id)

            if doc.status not in ("uploaded", "failed"):
                frappe.logger().info(
                    f"[Shorts] Skipping {short_id}; status={doc.status}"
                )
                return

            if not doc.file_key:
                raise Exception("Short file key is missing")

            # Move to processing
            doc.status = "processing"
            doc.processing_error = None
            doc.save(ignore_permissions=True)
            frappe.db.commit()

            minio = MinioService()
            input_url = minio.get_public_url(doc.file_key)

            if not input_url:
                raise Exception("Could not resolve source video URL")

            # DOWNLOAD VIDEO
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".mp4",
            ) as tmp_input:
                tmp_input_path = tmp_input.name

            cls._download_file(
                input_url,
                tmp_input_path,
            )

            # Validate file
            if (
                not os.path.exists(tmp_input_path)
                or os.path.getsize(tmp_input_path) == 0
            ):
                raise Exception("Downloaded file is empty")

            work_dir = tempfile.mkdtemp(
                prefix="aos_short_"
            )

            # METADATA
            duration = cls._get_duration(
                tmp_input_path
            )

            if duration is None or duration <= 0:
                raise Exception(
                    "Invalid video duration"
                )

            if duration > MAX_SHORT_DURATION_SECONDS:
                raise Exception(
                    f"Short must be <= "
                    f"{MAX_SHORT_DURATION_SECONDS} seconds"
                )

            has_audio = cls._has_audio(
                tmp_input_path
            )

            # THUMBNAIL
            thumbnail_path = os.path.join(
                work_dir,
                "thumbnail.jpg",
            )

            cls._generate_thumbnail(
                tmp_input_path,
                thumbnail_path,
            )

            # HLS GENERATION
            cls._generate_hls(
                input_file=tmp_input_path,
                work_dir=work_dir,
                has_audio=has_audio,
            )

            # Ensure master playlist exists
            master_playlist_path = os.path.join(
                work_dir,
                "master.m3u8",
            )

            if not os.path.exists(
                master_playlist_path
            ):
                raise Exception(
                    "HLS master playlist not generated"
                )

            # UPLOAD FILES
            base_key = (
                f"{minio.base_path}/processed/{doc.name}"
            )

            for root, _, files in os.walk(work_dir):
                for filename in files:
                    local_path = os.path.join(
                        root,
                        filename,
                    )

                    relative_path = os.path.relpath(
                        local_path,
                        work_dir,
                    ).replace("\\", "/")

                    remote_key = (
                        f"{base_key}/{relative_path}"
                    )

                    minio.upload_file(
                        remote_key,
                        local_path,
                    )

            # URLS
            playback_url = minio.get_public_url(
                f"{base_key}/master.m3u8"
            )

            thumbnail_url = None

            if os.path.exists(thumbnail_path):
                thumbnail_url = minio.get_public_url(
                    f"{base_key}/thumbnail.jpg"
                )

            # SAVE TO DB
            doc.reload()

            doc.playback_url = playback_url
            doc.thumbnail_url = thumbnail_url
            doc.duration_seconds = duration
            doc.status = "ready"
            doc.processing_error = None

            doc.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.logger().info(
                f"[Shorts] Completed: {short_id}"
            )

        except Exception as exc:
            error_text = (
                str(exc)
                or "Video processing failed"
            )

            frappe.log_error(
                frappe.get_traceback(),
                f"Video processing failed: {short_id}",
            )

            try:
                failed_doc = frappe.get_doc(
                    "AOS Short",
                    short_id,
                )

                failed_doc.status = "failed"
                failed_doc.processing_error = error_text

                failed_doc.save(
                    ignore_permissions=True
                )

                frappe.db.commit()

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Failed to update failed state",
                )

        finally:
            cls._cleanup(
                tmp_input_path,
                work_dir,
            )

    # DOWNLOAD
    @classmethod
    def _download_file(
        cls,
        url: str,
        destination_path: str,
    ):
        with requests.get(
            url,
            stream=True,
            timeout=cls.REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()

            with open(
                destination_path,
                "wb",
            ) as out:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    if chunk:
                        out.write(chunk)

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

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            frappe.logger().error(
                result.stderr
            )
            return None

        try:
            data = json.loads(
                result.stdout or "{}"
            )

            return float(
                data["format"]["duration"]
            )

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

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            return False

        try:
            data = json.loads(
                result.stdout or "{}"
            )

            return (
                len(data.get("streams", [])) > 0
            )

        except Exception:
            return False

    # THUMBNAIL
    @classmethod
    def _generate_thumbnail(
        cls,
        input_file: str,
        output_path: str,
    ):
        """
        Generate thumbnail safely.

        Handles:
        - short videos
        - mobile videos
        - variable frame-rate videos
        - rotated videos
        - videos without clean keyframes
        """

        attempts = [
            # Fast seek
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
            # Fallback to first decodable frame
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
            # Remove failed output before retry
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

            if (
                os.path.exists(output_path)
                and os.path.getsize(output_path) > 0
            ):
                return

            last_error = result.stderr or ""

        frappe.logger().error(
            "[Shorts] Thumbnail generation failed:\n"
            f"{last_error}"
        )

        raise Exception(
            "Thumbnail generation failed"
        )

    # HLS
    @classmethod
    def _generate_hls(
        cls,
        input_file: str,
        work_dir: str,
        has_audio: bool,
    ):
        if has_audio:
            audio_input = []

            audio_map = [
                "-map",
                "0:a",
            ]

        else:
            audio_input = [
                "-f",
                "lavfi",
                "-i",
                (
                    "anullsrc="
                    "channel_layout=stereo:"
                    "sample_rate=44100"
                ),
            ]

            audio_map = [
                "-map",
                "1:a",
            ]

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
            os.path.join(
                work_dir,
                "v%v_seg_%03d.ts",
            ),
            "-master_pl_name",
            "master.m3u8",
            "-var_stream_map",
            (
                "v:0,a:0 "
                "v:1,a:1 "
                "v:2,a:2"
            ),
            os.path.join(
                work_dir,
                "v%v.m3u8",
            ),
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            _, stderr = process.communicate(
                timeout=cls.FFMPEG_TIMEOUT_SECONDS
            )

        except subprocess.TimeoutExpired:
            process.kill()

            raise Exception(
                "FFmpeg timeout"
            )

        if process.returncode != 0:
            stderr_text = (
                stderr.decode("utf-8", errors="ignore")
                if stderr
                else ""
            )

            frappe.logger().error(
                "[Shorts] FFmpeg failed:\n"
                f"{stderr_text}"
            )

            raise Exception("FFmpeg failed")

    # CLEANUP
    @staticmethod
    def _cleanup(
        tmp_input_path: str | None,
        work_dir: str | None,
    ):
        try:
            if (
                tmp_input_path
                and os.path.exists(tmp_input_path)
            ):
                os.remove(tmp_input_path)

        except Exception:
            pass

        try:
            if (
                work_dir
                and os.path.exists(work_dir)
            ):
                shutil.rmtree(work_dir)

        except Exception:
            pass
