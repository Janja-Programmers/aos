"""Deprecated file-background-removal endpoint.

AOS-owned media no longer uses Frappe File for background removal. New clients
must upload the source image through aos.api.media.init_upload/confirm_upload and
then call aos.api.media.remove_background with media_id.

This wrapper accepts media_id for transitional clients but intentionally rejects
legacy file_id input so new backend work does not keep writing processed output
into the Frappe File filesystem.
"""

from __future__ import annotations

from aos.api.media.background import remove_background_impl as remove_media_background_impl
from aos.api.shared.responses import fail


def remove_background_impl(**kwargs):
    media_id = kwargs.get("media_id") or kwargs.get("id")
    if media_id:
        return remove_media_background_impl(**kwargs)

    return fail(
        "Background removal now uses media_id. Upload the source image through "
        "aos.api.media.init_upload, confirm it, then call "
        "aos.api.media.remove_background.",
        code="VALIDATION_ERROR",
        data={
            "required": "media_id",
            "new_endpoint": "aos.api.media.remove_background",
            "deprecated_endpoint": "aos.api.files.remove_background",
        },
    )
