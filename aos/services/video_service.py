from __future__ import annotations

from aos.services.video_processing_service import create_video_processing_job


class VideoService:
    """Compatibility facade for the external video-processing pipeline.

    Direct FFmpeg processing no longer runs inside Frappe. Existing call sites
    that still call ``VideoService.process_short`` now create a persistent
    AOS Video Processing Job and dispatch it to the external video service.
    """

    @classmethod
    def process_short(cls, short_id: str, force: bool = False):
        return create_video_processing_job(
            short_id=short_id,
            force=force,
            reason="audio_reprocess" if force else "short_upload",
            enqueue=True,
        )
