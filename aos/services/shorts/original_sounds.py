"""Server-owned original-sound lifecycle for Shorts.

A reusable original sound is created only from trusted video-processing output.
Clients never create or claim an original sound directly.  The source Short is
used as the idempotency key and the extracted audio is registered through the
canonical Media service before the Sound/Short-Sound relationship is created.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.media.media_service import MediaService
from aos.services.shorts.constants import SOUND_SOURCE_TYPE_ORIGINAL, SOUND_STATUS_ACTIVE

ORIGINAL_SOUND_MEDIA_PURPOSE = "sound_upload"
ORIGINAL_SOUND_TITLE_PREFIX = "original sound - "


def _value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _creator_display_name(user: str) -> str:
    """Return a privacy-safe creator label without ever exposing an email."""
    profile_name = frappe.db.get_value("AOS Profile", {"user": user}, "display_name")
    if profile_name:
        clean = str(profile_name).strip()
        if clean:
            return clean[:120]

    user_row = frappe.db.get_value(
        "User",
        user,
        ["full_name", "first_name"],
        as_dict=True,
    )
    for field in ("full_name", "first_name"):
        clean = str(_value(user_row, field) or "").strip()
        if clean:
            return clean[:120]

    return "AOS User"


def get_original_sound_id(short_id: str, *, active_only: bool = False) -> str | None:
    filters: dict[str, Any] = {
        "created_from_short": str(short_id or "").strip(),
        "source_type": SOUND_SOURCE_TYPE_ORIGINAL,
    }
    if active_only:
        filters["status"] = SOUND_STATUS_ACTIVE

    sound_id = frappe.db.get_value("AOS Sound", filters, "name", order_by="creation asc")
    return str(sound_id).strip() if sound_id else None


def _existing_short_sound(short_id: str) -> Any:
    return frappe.db.get_value(
        "AOS Short Sound",
        {"short": short_id},
        ["name", "sound", "is_original_audio"],
        as_dict=True,
    )


def _get_or_create_media(*, short: Any, audio: dict[str, Any]) -> object:
    bucket = str(audio.get("bucket") or "").strip().strip("/")
    object_key = str(audio.get("object_key") or "").strip().strip("/")

    existing_media_id = frappe.db.get_value(
        "AOS Media Object",
        {
            "bucket": bucket,
            "object_key": object_key,
            "purpose": ORIGINAL_SOUND_MEDIA_PURPOSE,
        },
        "name",
        order_by="creation asc",
    )
    service = MediaService()
    if existing_media_id:
        return service.get_media_doc(str(existing_media_id))

    return service.create_uploaded_from_existing_object(
        user=str(short.owner),
        purpose=ORIGINAL_SOUND_MEDIA_PURPOSE,
        filename=str(audio.get("filename") or f"{short.name}_original.m4a"),
        content_type=str(audio.get("content_type") or "audio/mp4"),
        bucket=bucket,
        object_key=object_key,
        size_bytes=int(audio.get("size_bytes") or 0),
        etag=str(audio.get("etag") or ""),
        duration_seconds=float(audio.get("duration_seconds") or 0),
        derived_from_media=str(getattr(short, "raw_video_media", "") or "") or None,
    )


def ensure_original_sound_for_short(*, short: Any, audio: dict[str, Any]) -> str | None:
    """Create and link one reusable original sound for a processed Short.

    The function is deliberately idempotent:
    * an existing Short-Sound link wins, so a selected sound is never replaced;
    * an existing original Sound for ``created_from_short`` is reused;
    * an already-registered Media object for the extracted object is reused.
    """
    short_id = str(getattr(short, "name", "") or "").strip()
    owner = str(getattr(short, "owner", "") or "").strip()
    if not short_id or not owner or not isinstance(audio, dict):
        return None

    existing_link = _existing_short_sound(short_id)
    if existing_link:
        if int(_value(existing_link, "is_original_audio", 0) or 0):
            sound_id = str(_value(existing_link, "sound") or "").strip()
            return sound_id or None
        # A creator-selected sound always takes precedence over auto-generated
        # original audio. Never replace it from a processing callback.
        return None

    sound_id = get_original_sound_id(short_id)
    if sound_id:
        status = frappe.db.get_value("AOS Sound", sound_id, "status")
        if status != SOUND_STATUS_ACTIVE:
            return None
    else:
        media = _get_or_create_media(short=short, audio=audio)
        display_name = _creator_display_name(owner)
        title = f"{ORIGINAL_SOUND_TITLE_PREFIX}{display_name}"[:140]
        artist = display_name[:140]

        sound = frappe.get_doc(
            {
                "doctype": "AOS Sound",
                "title": title,
                "artist": artist,
                "owner": owner,
                "source_type": SOUND_SOURCE_TYPE_ORIGINAL,
                "status": SOUND_STATUS_ACTIVE,
                "sound_media": media.name,
                "duration_seconds": float(audio.get("duration_seconds") or 0),
                "is_commercial_safe": 0,
                "created_from_short": short_id,
            }
        )
        sound.insert(ignore_permissions=True)
        sound_id = str(sound.name)

    # Re-check after Sound creation so a concurrently-created Short-Sound link
    # cannot be overwritten. The DocType also enforces short uniqueness.
    existing_link = _existing_short_sound(short_id)
    if existing_link:
        linked_sound = str(_value(existing_link, "sound") or "").strip()
        return linked_sound or None

    frappe.get_doc(
        {
            "doctype": "AOS Short Sound",
            "short": short_id,
            "sound": sound_id,
            "start_ms": 0,
            "duration_ms": 0,
            "volume": 1.0,
            "is_original_audio": 1,
        }
    ).insert(ignore_permissions=True)
    return sound_id
