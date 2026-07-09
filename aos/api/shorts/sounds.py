"""Sound / music APIs for Shorts.

Phase 4B stores sound metadata and triggers background audio/HLS remuxing
when a ready short changes its selected sound.
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.api.shared.sql_safety import safe_like_contains
from aos.api.shared.formatters import humanize_count
from aos.services.video_processing_service import create_video_processing_job

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)

from aos.api.shorts.constants import (
    SOUND_DEFAULT_LIMIT,
    SOUND_MAX_LIMIT,
    FAVORITE_SOUNDS_DEFAULT_LIMIT,
    FAVORITE_SOUNDS_MAX_LIMIT,
    SOUND_SHORTS_DEFAULT_LIMIT,
    SOUND_SHORTS_MAX_LIMIT,
    SOUND_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    SOUND_FAVORITE_TOGGLE_LIMIT_PER_MINUTE,
    CHANGE_SHORT_SOUND_LIMIT_PER_MINUTE_PER_USER,
    DEFAULT_SOUND_SOURCE_TYPE,
    SOUND_SOURCE_TYPE_LIBRARY,
    SOUND_SOURCE_TYPE_COMMERCIAL,
    SOUND_SOURCE_TYPE_UPLOADED,
    SOUND_SHAREABLE_STATUSES,
    SHORT_CONTENT_MODE_SHOP,
    SOUND_STAFF_ROLES,
)
from aos.api.shorts.validators import (
    validate_limit,
    validate_sound_title,
    validate_sound_artist,
    validate_sound_source_type,
    validate_sound_duration,
    validate_sound_timing,
)
from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.utils import build_cursor_where_clause, build_time_id_cursor


def _get_optional_viewer() -> str | None:
    user = current_user()
    if not user or user == "Guest":
        return None
    return user


def _is_staff(user: str | None) -> bool:
    if not user or user == "Guest":
        return False

    roles = set(frappe.get_roles(user) or [])
    return bool(roles.intersection(SOUND_STAFF_ROLES))


def _normalize_bool(value, *, default: int = 0) -> int:
    if value is None:
        return 1 if default else 0

    if isinstance(value, bool):
        return 1 if value else 0

    if isinstance(value, int):
        return 1 if value else 0

    value = str(value).strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return 1

    if value in {"0", "false", "no", "n", "off"}:
        return 0

    return 1 if default else 0




def _media_error_response(exc: Exception):
    if isinstance(exc, MediaNotFoundError):
        return safe_fail_from_exception(exc, fallback="Media not found.", error="NOT_FOUND")
    if isinstance(exc, MediaPermissionError):
        return safe_fail_from_exception(exc, fallback="Not allowed.", error="FORBIDDEN")
    if isinstance(exc, MediaValidationError):
        return safe_fail_from_exception(exc, fallback="Invalid media.", error="VALIDATION_ERROR")
    return None


def _active_sound_filters() -> dict[str, Any]:
    return {"status": ["in", list(SOUND_SHAREABLE_STATUSES)]}


def enqueue_short_audio_reprocess(short_id: str) -> None:
    """Queue background audio/HLS remuxing for a ready short.

    The short remains ready while this runs. Existing playback stays available
    until the external video-processing pipeline swaps in new HLS/final MP4 URLs.
    """
    if not short_id:
        return

    status = frappe.db.get_value("AOS Short", short_id, "status")
    if status != "ready":
        return

    try:
        values = {}
        meta = frappe.get_meta("AOS Short")
        if meta.has_field("audio_mix_status"):
            values["audio_mix_status"] = "pending"
        if meta.has_field("audio_mix_error"):
            values["audio_mix_error"] = None
        if values:
            frappe.db.set_value("AOS Short", short_id, values, update_modified=False)

        create_video_processing_job(
            short_id=short_id,
            force=True,
            reason="audio_reprocess",
            enqueue=True,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Failed to enqueue short audio reprocess: {short_id}",
        )


def validate_existing_short_sound_for_mode(*, short_id: str, content_mode: str):
    """Validate any existing linked sound against the target content mode.

    Used when a short is converted to shop mode without passing a new sound_id.
    Shop shorts may only keep active commercial-safe sounds.
    """
    if content_mode != SHORT_CONTENT_MODE_SHOP:
        return None

    row = frappe.db.sql(
        """
        SELECT
            ss.sound,
            snd.status,
            snd.is_commercial_safe
        FROM `tabAOS Short Sound` ss
        INNER JOIN `tabAOS Sound` snd ON snd.name = ss.sound
        WHERE ss.short = %s
        LIMIT 1
        """,
        (short_id,),
        as_dict=True,
    )

    if not row:
        return None

    sound = row[0]

    if sound.get("status") != "active":
        return fail(
            "Shop shorts can only use active commercial-safe sounds.",
            error="VALIDATION_ERROR",
        )

    if not int(sound.get("is_commercial_safe") or 0):
        return fail(
            "Shop shorts can only use commercial-safe sounds.",
            error="VALIDATION_ERROR",
        )

    return None


def _load_favorite_sound_ids(viewer: str | None, sound_ids: list[str]) -> set[str]:
    if not viewer or not sound_ids:
        return set()

    rows = frappe.get_all(
        "AOS Sound Favorite",
        filters={"user": viewer, "sound": ["in", sound_ids]},
        pluck="sound",
    )
    return set(rows or [])


def serialize_sound_row(
    row: dict[str, Any],
    *,
    viewer: str | None = None,
    favorited_sound_ids: set[str] | None = None,
) -> dict[str, Any]:
    sound_id = row.get("name") or row.get("sound")
    favorited_sound_ids = favorited_sound_ids or set()

    return {
        "id": sound_id,
        "title": row.get("title") or "",
        "artist": row.get("artist") or "",
        "source_type": row.get("source_type") or DEFAULT_SOUND_SOURCE_TYPE,
        "file_url": row.get("file_url"),
        "sound_media": row.get("sound_media"),
        "sound_media_id": row.get("sound_media"),
        "media": row.get("sound_media"),
        "media_id": row.get("sound_media"),
        "duration_seconds": float(row.get("duration_seconds") or 0),
        "usage_count": int(row.get("usage_count") or 0),
        "usage_count_display": humanize_count(row.get("usage_count") or 0),
        "favorite_count": int(row.get("favorite_count") or 0),
        "favorite_count_display": humanize_count(row.get("favorite_count") or 0),
        "status": row.get("status"),
        "is_commercial_safe": bool(int(row.get("is_commercial_safe") or 0)),
        "owner": row.get("owner"),
        "created_from_short": row.get("created_from_short"),
        "viewer_state": {
            "is_favorited": bool(sound_id and sound_id in favorited_sound_ids),
            "can_favorite": bool(viewer),
        },
    }


def _serialize_sound_rows(rows: list[dict[str, Any]], *, viewer: str | None) -> list[dict[str, Any]]:
    sound_ids = [row.get("name") for row in rows if row.get("name")]
    favorited = _load_favorite_sound_ids(viewer, sound_ids)
    return [serialize_sound_row(row, viewer=viewer, favorited_sound_ids=favorited) for row in rows]


def get_short_sound_map(short_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-load sound info by short id for feed/detail serializers."""
    if not short_ids:
        return {}

    rows = frappe.db.sql(
        """
        SELECT
            ss.short,
            ss.sound,
            ss.start_ms,
            ss.duration_ms,
            ss.volume,
            ss.is_original_audio,
            snd.title,
            snd.artist,
            snd.source_type,
            snd.file_url,
            snd.sound_media,
            snd.duration_seconds,
            snd.usage_count,
            snd.favorite_count,
            snd.status,
            snd.is_commercial_safe,
            snd.owner,
            snd.created_from_short
        FROM `tabAOS Short Sound` ss
        INNER JOIN `tabAOS Sound` snd ON snd.name = ss.sound
        WHERE ss.short IN %(short_ids)s
          AND snd.status = 'active'
        """,
        {"short_ids": tuple(short_ids)},
        as_dict=True,
    )

    result: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        sound = serialize_sound_row(
            {
                "name": row.get("sound"),
                "title": row.get("title"),
                "artist": row.get("artist"),
                "source_type": row.get("source_type"),
                "file_url": row.get("file_url"),
                "sound_media": row.get("sound_media"),
                "duration_seconds": row.get("duration_seconds"),
                "usage_count": row.get("usage_count"),
                "favorite_count": row.get("favorite_count"),
                "status": row.get("status"),
                "is_commercial_safe": row.get("is_commercial_safe"),
                "owner": row.get("owner"),
                "created_from_short": row.get("created_from_short"),
            },
            viewer=None,
            favorited_sound_ids=set(),
        )
        sound.update(
            {
                "start_ms": int(row.get("start_ms") or 0),
                "duration_ms": int(row.get("duration_ms") or 0),
                "volume": float(row.get("volume") if row.get("volume") is not None else 1.0),
                "is_original_audio": bool(int(row.get("is_original_audio") or 0)),
            }
        )
        # Sound info nested inside short should not depend on caller favorites.
        sound.pop("viewer_state", None)
        result[row.get("short")] = sound

    return result


def _validate_sound_for_short(*, short: Any, sound_id: str):
    sound = frappe.db.get_value(
        "AOS Sound",
        sound_id,
        ["name", "status", "is_commercial_safe"],
        as_dict=True,
    )
    if not sound:
        return None, fail("Sound not found.", error="NOT_FOUND")

    if sound.status != "active":
        return None, fail("Sound is not active.", error="VALIDATION_ERROR")

    content_mode = getattr(short, "content_mode", None)
    if isinstance(short, dict):
        content_mode = short.get("content_mode")

    if content_mode == SHORT_CONTENT_MODE_SHOP and not int(sound.is_commercial_safe or 0):
        return None, fail(
            "Shop shorts can only use commercial-safe sounds.",
            error="VALIDATION_ERROR",
        )

    return sound, None


def set_short_sound(
    *,
    short_id: str,
    sound_id: str,
    start_ms=0,
    duration_ms=0,
    volume=1.0,
    is_original_audio: int = 0,
) -> dict[str, Any]:
    """Replace the sound metadata link for a short.

    This does not remix video audio yet. It only records the selected sound.
    """
    short = frappe.get_doc("AOS Short", short_id)

    sound, err = _validate_sound_for_short(short=short, sound_id=sound_id)
    if err:
        raise frappe.ValidationError(err.get("message") if isinstance(err, dict) else "Invalid sound")

    start_ms, duration_ms, volume, err = validate_sound_timing(
        start_ms=start_ms,
        duration_ms=duration_ms,
        volume=volume,
    )
    if err:
        raise frappe.ValidationError(err.get("message") if isinstance(err, dict) else "Invalid sound timing")

    existing = frappe.get_all(
        "AOS Short Sound",
        filters={"short": short_id},
        fields=["name"],
        limit=1,
    )

    for row in existing:
        frappe.delete_doc("AOS Short Sound", row.name, ignore_permissions=True)

    doc = frappe.get_doc(
        {
            "doctype": "AOS Short Sound",
            "short": short_id,
            "sound": sound_id,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "volume": volume,
            "is_original_audio": 1 if is_original_audio else 0,
        }
    )
    doc.insert(ignore_permissions=True)

    sound_row = frappe.db.get_value(
        "AOS Sound",
        sound_id,
        [
            "name",
            "title",
            "artist",
            "source_type",
            "file_url",
            "sound_media",
            "duration_seconds",
            "usage_count",
            "favorite_count",
            "status",
            "is_commercial_safe",
            "owner",
            "created_from_short",
        ],
        as_dict=True,
    )
    serialized = serialize_sound_row(sound_row or {}, viewer=None)
    serialized.update(
        {
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "volume": volume,
            "is_original_audio": bool(is_original_audio),
        }
    )
    serialized.pop("viewer_state", None)
    return serialized


def remove_short_sound_link(short_id: str) -> None:
    existing = frappe.get_all(
        "AOS Short Sound",
        filters={"short": short_id},
        fields=["name"],
    )
    for row in existing:
        frappe.delete_doc("AOS Short Sound", row.name, ignore_permissions=True)


# SOUND CREATION

def create_sound_impl(**kwargs):
    """Create a reusable sound from uploaded sound media.

    Required client flow:
    1. aos.api.media.init_upload with purpose=sound_upload
    2. PUT audio to the returned upload_url
    3. aos.api.media.confirm_upload with media_id
    4. aos.api.shorts.create_sound with sound_media/media_id and metadata
    """
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:sound_create:user:{user}",
        ttl_seconds=60,
        limit=SOUND_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    media_id, err = require_id(
        kwargs.get("sound_media")
        or kwargs.get("media_id")
        or kwargs.get("media"),
        "sound_media",
    )
    if err:
        return err

    title, err = validate_sound_title(kwargs.get("title"))
    if err:
        return err

    artist, err = validate_sound_artist(kwargs.get("artist"))
    if err:
        return err

    source_type, err = validate_sound_source_type(kwargs.get("source_type"))
    if err:
        return err

    duration_seconds, err = validate_sound_duration(kwargs.get("duration_seconds"))
    if err:
        return err

    staff = _is_staff(user)
    if source_type in {SOUND_SOURCE_TYPE_LIBRARY, SOUND_SOURCE_TYPE_COMMERCIAL} and not staff:
        return fail("Only staff can create library/commercial sounds.", error="FORBIDDEN")

    is_commercial_safe = _normalize_bool(kwargs.get("is_commercial_safe"), default=0)
    if source_type == SOUND_SOURCE_TYPE_COMMERCIAL:
        is_commercial_safe = 1
    elif not staff:
        is_commercial_safe = 0

    try:
        media_service = MediaService()

        try:
            media_doc = media_service.confirm_upload(user=user, media_id=media_id)
        except (MediaValidationError, MediaPermissionError, MediaNotFoundError) as exc:
            media_err = _media_error_response(exc)
            if media_err:
                return media_err
            return fail("Upload not completed or file missing.", error="FILE_MISSING")

        if media_doc.purpose != "sound_upload":
            return fail("Sound media has the wrong purpose.", error="VALIDATION_ERROR")
        if media_doc.visibility != "Public":
            return fail("Sound media must be public.", error="VALIDATION_ERROR")
        if media_doc.status != "Uploaded":
            return fail("Sound media must be uploaded before creating a sound.", error="VALIDATION_ERROR")

        file_key = media_doc.object_key
        file_url = media_doc.public_url or media_service.get_url(media_id=media_doc.name, user=user)

        doc = frappe.get_doc(
            {
                "doctype": "AOS Sound",
                "title": title,
                "artist": artist,
                "owner": user,
                "source_type": source_type or SOUND_SOURCE_TYPE_UPLOADED,
                "status": "active",
                "sound_media": media_doc.name,
                "file_key": file_key,
                "file_url": file_url,
                "duration_seconds": duration_seconds,
                "is_commercial_safe": is_commercial_safe,
            }
        )
        doc.insert(ignore_permissions=True)

        media_service.attach_media(
            media_id=media_doc.name,
            user=user,
            purpose="sound_upload",
            attached_doctype="AOS Sound",
            attached_name=doc.name,
            attached_field="sound_media",
        )

        frappe.db.commit()

        row = frappe.db.get_value(
            "AOS Sound",
            doc.name,
            [
                "name",
                "title",
                "artist",
                "source_type",
                "file_url",
                "sound_media",
                "duration_seconds",
                "usage_count",
                "favorite_count",
                "status",
                "is_commercial_safe",
                "owner",
                "created_from_short",
            ],
            as_dict=True,
        )

        return ok("Sound created.", data={"sound": serialize_sound_row(row, viewer=user)})

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "create_sound failed")
        return fail("Failed to create sound.", error="INTERNAL_ERROR")


# SOUND BROWSING

def list_sounds_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:sounds:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=300,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    viewer = _get_optional_viewer()
    limit = validate_limit(kwargs.get("limit"), SOUND_DEFAULT_LIMIT, SOUND_MAX_LIMIT)
    cursor = kwargs.get("cursor")
    source_type = kwargs.get("source_type")

    source_clause = ""
    source_params: tuple = ()
    if source_type and str(source_type).strip().lower() != "all":
        source_type, err = validate_sound_source_type(source_type)
        if err:
            return err
        source_clause = "AND source_type = %s"
        source_params = (source_type,)

    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="creation",
        name_field="name",
        cursor=cursor,
    )

    try:
        rows = frappe.db.sql(
            f"""
            SELECT
                name, title, artist, source_type, file_url, sound_media, duration_seconds,
                usage_count, favorite_count, status, is_commercial_safe,
                owner, created_from_short, creation
            FROM `tabAOS Sound`
            WHERE status = 'active'
              {source_clause}
              {where_cursor}
            ORDER BY creation DESC, name DESC
            LIMIT %s
            """,
            (*source_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(created_on=last.creation, name=last.name)

        return ok(
            "Sounds fetched.",
            data={
                "items": _serialize_sound_rows(visible_rows, viewer=viewer),
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "list_sounds failed")
        return fail("Failed to fetch sounds", error="INTERNAL_ERROR")


def search_sounds_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:sounds:search:ip:{request_ip()}",
        ttl_seconds=60,
        limit=300,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    q = str(kwargs.get("q") or kwargs.get("query") or "").strip()
    if not q:
        return fail("Search query is required.", error="VALIDATION_ERROR")

    viewer = _get_optional_viewer()
    limit = validate_limit(kwargs.get("limit"), SOUND_DEFAULT_LIMIT, SOUND_MAX_LIMIT)
    like = safe_like_contains(q)

    try:
        rows = frappe.db.sql(
            """
            SELECT
                name, title, artist, source_type, file_url, sound_media, duration_seconds,
                usage_count, favorite_count, status, is_commercial_safe,
                owner, created_from_short, creation
            FROM `tabAOS Sound`
            WHERE status = 'active'
              AND (title LIKE %s ESCAPE '\\' OR artist LIKE %s ESCAPE '\\')
            ORDER BY creation DESC, name DESC
            LIMIT %s
            """,
            (like, like, limit),
            as_dict=True,
        )
        return ok("Sounds fetched.", data={"items": _serialize_sound_rows(rows, viewer=viewer)})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "search_sounds failed")
        return fail("Failed to search sounds", error="INTERNAL_ERROR")


def get_sound_impl(**kwargs):
    sound_id, err = require_id(kwargs.get("sound_id"), "sound_id")
    if err:
        return err

    viewer = _get_optional_viewer()

    try:
        row = frappe.db.get_value(
            "AOS Sound",
            sound_id,
            [
                "name",
                "title",
                "artist",
                "source_type",
                "file_url",
                "sound_media",
                "duration_seconds",
                "usage_count",
                "favorite_count",
                "status",
                "is_commercial_safe",
                "owner",
                "created_from_short",
            ],
            as_dict=True,
        )
        if not row or row.status != "active":
            return fail("Sound not found.", error="NOT_FOUND")

        favorites = _load_favorite_sound_ids(viewer, [sound_id])
        return ok("Sound fetched.", data={"sound": serialize_sound_row(row, viewer=viewer, favorited_sound_ids=favorites)})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "get_sound failed")
        return fail("Failed to fetch sound", error="INTERNAL_ERROR")


def favorite_sound_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:sound_favorite:user:{user}",
        ttl_seconds=60,
        limit=SOUND_FAVORITE_TOGGLE_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    sound_id, err = require_id(kwargs.get("sound_id"), "sound_id")
    if err:
        return err

    try:
        sound = frappe.db.get_value("AOS Sound", sound_id, ["name", "status"], as_dict=True)
        if not sound or sound.status != "active":
            return fail("Sound not found.", error="NOT_FOUND")

        existing = frappe.get_all(
            "AOS Sound Favorite",
            filters={"sound": sound_id, "user": user},
            fields=["name"],
            limit=1,
        )
        if existing:
            frappe.delete_doc("AOS Sound Favorite", existing[0].name, ignore_permissions=True)
            favorited = False
            message = "Removed from favorite sounds."
        else:
            frappe.get_doc({"doctype": "AOS Sound Favorite", "sound": sound_id, "user": user}).insert(ignore_permissions=True)
            favorited = True
            message = "Sound favorited."

        frappe.db.commit()
        favorite_count = frappe.db.get_value("AOS Sound", sound_id, "favorite_count") or 0

        return ok(
            message,
            data={
                "sound_id": sound_id,
                "favorited": favorited,
                "viewer_state": {"is_favorited": favorited},
                "metrics": {
                    "favorite_count": int(favorite_count),
                    "favorite_count_display": humanize_count(favorite_count),
                },
            },
        )
    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "favorite_sound failed")
        return fail("Failed to toggle favorite sound", error="INTERNAL_ERROR")


def my_favorite_sounds_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        FAVORITE_SOUNDS_DEFAULT_LIMIT,
        FAVORITE_SOUNDS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")
    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="fav.creation",
        name_field="fav.name",
        cursor=cursor,
    )

    try:
        rows = frappe.db.sql(
            f"""
            SELECT
                snd.name, snd.title, snd.artist, snd.source_type, snd.file_url, snd.sound_media,
                snd.duration_seconds, snd.usage_count, snd.favorite_count,
                snd.status, snd.is_commercial_safe, snd.owner, snd.created_from_short,
                fav.creation AS action_creation, fav.name AS action_name
            FROM `tabAOS Sound Favorite` fav
            INNER JOIN `tabAOS Sound` snd ON snd.name = fav.sound
            WHERE fav.user = %s
              AND snd.status = 'active'
              {where_cursor}
            ORDER BY fav.creation DESC, fav.name DESC
            LIMIT %s
            """,
            (user, *params_cursor, limit + 1),
            as_dict=True,
        )
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(created_on=last.action_creation, name=last.action_name)

        favorited = {row.name for row in visible_rows if row.name}
        return ok(
            "Favorite sounds fetched.",
            data={
                "items": [serialize_sound_row(row, viewer=user, favorited_sound_ids=favorited) for row in visible_rows],
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "my_favorite_sounds failed")
        return fail("Failed to fetch favorite sounds", error="INTERNAL_ERROR")


def sound_shorts_impl(**kwargs):
    sound_id, err = require_id(kwargs.get("sound_id"), "sound_id")
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        SOUND_SHORTS_DEFAULT_LIMIT,
        SOUND_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")
    viewer = _get_optional_viewer()

    if not frappe.db.exists("AOS Sound", {"name": sound_id, "status": "active"}):
        return fail("Sound not found.", error="NOT_FOUND")

    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="s.creation",
        name_field="s.name",
        cursor=cursor,
    )

    try:
        from aos.api.shorts.management import (
            _select_short_rows_sql,
            _serialize_rows_with_viewer_state,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}
            INNER JOIN `tabAOS Short Sound` ss ON ss.short = s.name

            WHERE
                ss.sound = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (sound_id, *params_cursor, limit + 1),
            as_dict=True,
        )

        safe_rows = []
        for row in rows or []:
            if can_view_short(row, current_user=viewer):
                safe_rows.append(row)
                if len(safe_rows) >= limit + 1:
                    break

        has_more = len(safe_rows) > limit
        visible_rows = safe_rows[:limit]
        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(created_on=last.creation, name=last.name)

        return ok(
            "Sound shorts fetched.",
            data={
                "items": _serialize_rows_with_viewer_state(visible_rows, viewer=viewer),
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "sound_shorts failed")
        return fail("Failed to fetch sound shorts", error="INTERNAL_ERROR")


# SHORT SOUND LINKING

def change_short_sound_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:change_sound:user:{user}",
        ttl_seconds=60,
        limit=CHANGE_SHORT_SOUND_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    sound_id, err = require_id(kwargs.get("sound_id"), "sound_id")
    if err:
        return err

    try:
        short = frappe.get_doc("AOS Short", short_id)
        if short.owner != user:
            return fail("Not allowed.", error="FORBIDDEN")

        if short.status == "deleted" or short.visibility_status == "deleted":
            return fail("Cannot change sound on a deleted short.", error="VALIDATION_ERROR")

        sound = set_short_sound(
            short_id=short_id,
            sound_id=sound_id,
            start_ms=kwargs.get("sound_start_ms") or kwargs.get("start_ms"),
            duration_ms=kwargs.get("sound_duration_ms") or kwargs.get("duration_ms"),
            volume=kwargs.get("sound_volume") if kwargs.get("sound_volume") is not None else kwargs.get("volume"),
        )
        frappe.db.commit()
        enqueue_short_audio_reprocess(short_id)
        return ok(
            "Short sound updated.",
            data={
                "short_id": short_id,
                "sound": sound,
                "audio_mix_status": "pending" if short.status == "ready" else None,
            },
        )
    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")
    except frappe.DoesNotExistError:
        return fail("Short not found.", error="NOT_FOUND")
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "change_short_sound failed")
        return fail("Failed to change short sound", error="INTERNAL_ERROR")


def remove_short_sound_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        short = frappe.get_doc("AOS Short", short_id)
        if short.owner != user:
            return fail("Not allowed.", error="FORBIDDEN")

        if short.status == "deleted" or short.visibility_status == "deleted":
            return fail("Cannot remove sound from a deleted short.", error="VALIDATION_ERROR")

        remove_short_sound_link(short_id)
        frappe.db.commit()
        enqueue_short_audio_reprocess(short_id)
        return ok(
            "Short sound removed.",
            data={
                "short_id": short_id,
                "sound": None,
                "audio_mix_status": "pending" if short.status == "ready" else None,
            },
        )
    except frappe.DoesNotExistError:
        return fail("Short not found.", error="NOT_FOUND")
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "remove_short_sound failed")
        return fail("Failed to remove short sound", error="INTERNAL_ERROR")
