"""Activity Center persistence service.

Feature doctypes remain the source of truth. ``AOS User Activity`` is a private,
user-owned history projection. The service never commits or rolls back the
caller's transaction.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.activity.constants import (
    ACTIVE_STATUS,
    ACTIVITY_DOCTYPE,
    CLEARED_STATUS,
    CLEAR_BATCH_SIZE,
    HIDDEN_STATUS,
    METADATA_JSON_MAX_BYTES,
    ROUTE_ID_MAX_LEN,
    ROUTE_TYPE_MAX_LEN,
    TARGET_IMAGE_MAX_LEN,
    TARGET_SUBTITLE_MAX_LEN,
    TARGET_TEXT_MAX_LEN,
    UNIQUE_KEY_MAX_LEN,
    VALID_ACTIVITY_GROUPS,
    VALID_ACTIVITY_STATUSES,
)
from aos.services.activity.serializers import serialize_activity


class ActivityService:
    """Record, de-duplicate, serialize, hide, and clear Activity Center rows."""

    @staticmethod
    def normalize_group(value: str | None) -> str:
        value = (value or "").strip()
        return value if value in VALID_ACTIVITY_GROUPS else "Other"

    @staticmethod
    def normalize_type(value: str | None) -> str:
        return (value or "").strip()[:80]

    @staticmethod
    def normalize_status(value: str | None) -> str:
        value = (value or ACTIVE_STATUS).strip()
        return value if value in VALID_ACTIVITY_STATUSES else ACTIVE_STATUS

    @staticmethod
    def _text(value: Any, *, max_len: int) -> str:
        return str(value or "").strip()[:max_len]

    @classmethod
    def _bounded_metadata(cls, metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(metadata.items())[:50]:
            key = cls._text(raw_key, max_len=80)
            if not key:
                continue
            if raw_value is None or isinstance(raw_value, (bool, int, float)):
                value = raw_value
            elif isinstance(raw_value, str):
                value = raw_value[:500]
            else:
                # Activity metadata is a compact presentation snapshot, not an
                # arbitrary document store.
                continue
            candidate = {**result, key: value}
            encoded = json.dumps(candidate, ensure_ascii=False, default=str).encode("utf-8")
            if len(encoded) > METADATA_JSON_MAX_BYTES:
                break
            result[key] = value
        return result

    @staticmethod
    def build_unique_key(
        *,
        activity_type: str,
        target_doctype: str | None = None,
        target_name: str | None = None,
        route_type: str | None = None,
        route_id: str | None = None,
    ) -> str:
        """Build a stable key for one user's repeatable activity target."""
        parts = [
            (activity_type or "").strip(),
            (target_doctype or "").strip(),
            (target_name or "").strip(),
            (route_type or "").strip(),
            (route_id or "").strip(),
        ]
        material = "|".join(parts).strip("|")
        if len(material) <= UNIQUE_KEY_MAX_LEN:
            return material
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def active_key_for(*, user: str, unique_key: str) -> str | None:
        user = str(user or "").strip()
        unique_key = str(unique_key or "").strip()
        if not user or not unique_key:
            return None
        return hashlib.sha256(f"{user}\x1f{unique_key}".encode("utf-8")).hexdigest()

    @classmethod
    def _lock_active_by_key(cls, active_key: str | None) -> str | None:
        if not active_key:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT name
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE active_key = %s AND status = %s
            LIMIT 1
            FOR UPDATE
            """,
            (active_key, ACTIVE_STATUS),
            as_dict=True,
        )
        return str(rows[0].name) if rows else None

    @classmethod
    def record_activity(
        cls,
        *,
        user: str,
        activity_group: str,
        activity_type: str,
        target_doctype: str | None = None,
        target_name: str | None = None,
        target_title: str | None = None,
        target_subtitle: str | None = None,
        target_image: str | None = None,
        route_type: str | None = None,
        route_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at=None,
        unique_key: str | None = None,
    ):
        """Create one private history row; duplicate unique events are idempotent."""
        user = str(user or "").strip()
        activity_type = cls.normalize_type(activity_type)
        if not user or not activity_type:
            return None

        unique_key = cls._text(unique_key, max_len=UNIQUE_KEY_MAX_LEN)
        active_key = cls.active_key_for(user=user, unique_key=unique_key)
        existing = cls._lock_active_by_key(active_key)
        if existing:
            return existing

        occurred_at = occurred_at or now_datetime()
        values = {
            "doctype": ACTIVITY_DOCTYPE,
            "user": user,
            "activity_group": cls.normalize_group(activity_group),
            "activity_type": activity_type,
            "status": ACTIVE_STATUS,
            "occurred_at": occurred_at,
            "last_occurrence_at": occurred_at,
            "count": 1,
            "target_doctype": cls._text(target_doctype, max_len=TARGET_TEXT_MAX_LEN),
            "target_name": cls._text(target_name, max_len=TARGET_TEXT_MAX_LEN),
            "target_title": cls._text(target_title, max_len=TARGET_TEXT_MAX_LEN),
            "target_subtitle": cls._text(target_subtitle, max_len=TARGET_SUBTITLE_MAX_LEN),
            "target_image": cls._text(target_image, max_len=TARGET_IMAGE_MAX_LEN),
            "route_type": cls._text(route_type, max_len=ROUTE_TYPE_MAX_LEN),
            "route_id": cls._text(route_id, max_len=ROUTE_ID_MAX_LEN),
            "metadata_json": cls._bounded_metadata(metadata),
            "unique_key": unique_key,
            "active_key": active_key,
        }
        doc = frappe.get_doc(values)
        try:
            doc.insert(ignore_permissions=True)
            return doc.name
        except Exception as exc:
            if not active_key or not is_duplicate_entry_error(exc):
                raise
            # Concurrent retry won the unique active-key insert. Return the
            # authoritative row rather than failing the primary domain action.
            existing = cls._lock_active_by_key(active_key)
            if existing:
                return existing
            raise

    @classmethod
    def record_or_update_activity(
        cls,
        *,
        user: str,
        activity_group: str,
        activity_type: str,
        target_doctype: str | None = None,
        target_name: str | None = None,
        target_title: str | None = None,
        target_subtitle: str | None = None,
        target_image: str | None = None,
        route_type: str | None = None,
        route_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at=None,
        unique_key: str | None = None,
    ):
        """Atomically coalesce repeatable activity into one active row."""
        user = str(user or "").strip()
        activity_type = cls.normalize_type(activity_type)
        if not user or not activity_type:
            return None

        unique_key = (
            unique_key
            or cls.build_unique_key(
                activity_type=activity_type,
                target_doctype=target_doctype,
                target_name=target_name,
                route_type=route_type,
                route_id=route_id,
            )
        )
        unique_key = cls._text(unique_key, max_len=UNIQUE_KEY_MAX_LEN)
        active_key = cls.active_key_for(user=user, unique_key=unique_key)

        existing = cls._lock_active_by_key(active_key)
        if existing:
            cls._atomic_update_existing_activity(
                activity_id=existing,
                occurred_at=occurred_at,
                activity_group=activity_group,
                target_doctype=target_doctype,
                target_name=target_name,
                target_title=target_title,
                target_subtitle=target_subtitle,
                target_image=target_image,
                route_type=route_type,
                route_id=route_id,
                metadata=metadata,
            )
            return existing

        occurred_at = occurred_at or now_datetime()
        doc = frappe.get_doc(
            {
                "doctype": ACTIVITY_DOCTYPE,
                "user": user,
                "activity_group": cls.normalize_group(activity_group),
                "activity_type": activity_type,
                "status": ACTIVE_STATUS,
                "occurred_at": occurred_at,
                "last_occurrence_at": occurred_at,
                "count": 1,
                "target_doctype": cls._text(target_doctype, max_len=TARGET_TEXT_MAX_LEN),
                "target_name": cls._text(target_name, max_len=TARGET_TEXT_MAX_LEN),
                "target_title": cls._text(target_title, max_len=TARGET_TEXT_MAX_LEN),
                "target_subtitle": cls._text(target_subtitle, max_len=TARGET_SUBTITLE_MAX_LEN),
                "target_image": cls._text(target_image, max_len=TARGET_IMAGE_MAX_LEN),
                "route_type": cls._text(route_type, max_len=ROUTE_TYPE_MAX_LEN),
                "route_id": cls._text(route_id, max_len=ROUTE_ID_MAX_LEN),
                "metadata_json": cls._bounded_metadata(metadata),
                "unique_key": unique_key,
                "active_key": active_key,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
            return doc.name
        except Exception as exc:
            if not active_key or not is_duplicate_entry_error(exc):
                raise
            # A concurrent repeat inserted the same active key. Once that row
            # is committed, lock it and count this request as another
            # occurrence instead of dropping the event.
            existing = cls._lock_active_by_key(active_key)
            if not existing:
                raise
            cls._atomic_update_existing_activity(
                activity_id=existing,
                occurred_at=occurred_at,
                activity_group=activity_group,
                target_doctype=target_doctype,
                target_name=target_name,
                target_title=target_title,
                target_subtitle=target_subtitle,
                target_image=target_image,
                route_type=route_type,
                route_id=route_id,
                metadata=metadata,
            )
            return existing

    @classmethod
    def _atomic_update_existing_activity(
        cls,
        *,
        activity_id: str,
        occurred_at=None,
        activity_group: str | None = None,
        target_doctype: str | None = None,
        target_name: str | None = None,
        target_title: str | None = None,
        target_subtitle: str | None = None,
        target_image: str | None = None,
        route_type: str | None = None,
        route_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = occurred_at or now_datetime()
        set_clauses = [
            "activity_group = %s",
            "last_occurrence_at = %s",
            "`count` = GREATEST(COALESCE(`count`, 0), 0) + 1",
            "modified = NOW()",
            "modified_by = %s",
        ]
        params: list[Any] = [
            cls.normalize_group(activity_group),
            now,
            frappe.session.user or "Administrator",
        ]
        optional_fields = {
            "target_doctype": (target_doctype, TARGET_TEXT_MAX_LEN),
            "target_name": (target_name, TARGET_TEXT_MAX_LEN),
            "target_title": (target_title, TARGET_TEXT_MAX_LEN),
            "target_subtitle": (target_subtitle, TARGET_SUBTITLE_MAX_LEN),
            "target_image": (target_image, TARGET_IMAGE_MAX_LEN),
            "route_type": (route_type, ROUTE_TYPE_MAX_LEN),
            "route_id": (route_id, ROUTE_ID_MAX_LEN),
        }
        for fieldname, (value, max_len) in optional_fields.items():
            if value is not None:
                set_clauses.append(f"`{fieldname}` = %s")
                params.append(cls._text(value, max_len=max_len))
        if metadata is not None:
            set_clauses.append("metadata_json = %s")
            params.append(frappe.as_json(cls._bounded_metadata(metadata)))
        params.extend([activity_id, ACTIVE_STATUS])
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET {', '.join(set_clauses)}
            WHERE name = %s AND status = %s
            """,
            params,
        )

    @staticmethod
    def serialize_activity(row) -> dict[str, Any]:
        return serialize_activity(row)

    @classmethod
    def hide_activity(cls, *, user: str, activity_id: str) -> bool:
        user = str(user or "").strip()
        activity_id = str(activity_id or "").strip()
        if not user or not activity_id:
            return False
        rows = frappe.db.sql(
            f"""
            SELECT name, status
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE name = %s AND user = %s
            LIMIT 1
            FOR UPDATE
            """,
            (activity_id, user),
            as_dict=True,
        )
        if not rows:
            return False
        if rows[0].status != ACTIVE_STATUS:
            return True
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET status = %s, active_key = NULL, modified = NOW(), modified_by = %s
            WHERE name = %s AND user = %s AND status = %s
            """,
            (HIDDEN_STATUS, frappe.session.user or user, activity_id, user, ACTIVE_STATUS),
        )
        return True

    @classmethod
    def hide_activity_by_unique_key(cls, *, user: str, unique_key: str) -> bool:
        user = str(user or "").strip()
        unique_key = cls._text(unique_key, max_len=UNIQUE_KEY_MAX_LEN)
        active_key = cls.active_key_for(user=user, unique_key=unique_key)
        if not active_key:
            return False
        activity_id = cls._lock_active_by_key(active_key)
        if not activity_id:
            return False
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET status = %s, active_key = NULL, modified = NOW(), modified_by = %s
            WHERE name = %s AND status = %s
            """,
            (HIDDEN_STATUS, frappe.session.user or user, activity_id, ACTIVE_STATUS),
        )
        return True

    @classmethod
    def clear_activity(
        cls,
        *,
        user: str,
        activity_group: str | None = None,
        activity_type: str | None = None,
    ) -> int:
        """Mark active rows as cleared in bounded, locked batches."""
        user = str(user or "").strip()
        if not user:
            return 0
        # Freeze the clear set at operation start. Concurrent/new activity is
        # intentionally left active instead of making this loop chase a moving
        # target indefinitely.
        cutoff = now_datetime()
        conditions = ["user = %s", "status = %s", "creation <= %s"]
        params: list[Any] = [user, ACTIVE_STATUS, cutoff]
        if activity_group:
            conditions.append("activity_group = %s")
            params.append(cls.normalize_group(activity_group))
        if activity_type:
            conditions.append("activity_type = %s")
            params.append(cls.normalize_type(activity_type))
        where_sql = " AND ".join(conditions)
        total = 0
        while True:
            rows = frappe.db.sql(
                f"""
                SELECT name
                FROM `tab{ACTIVITY_DOCTYPE}`
                WHERE {where_sql}
                ORDER BY name ASC
                LIMIT %s
                FOR UPDATE
                """,
                [*params, CLEAR_BATCH_SIZE],
                pluck=True,
            )
            names = tuple(str(name) for name in rows if name)
            if not names:
                break
            frappe.db.sql(
                f"""
                UPDATE `tab{ACTIVITY_DOCTYPE}`
                SET status = %(cleared)s, active_key = NULL, modified = NOW(), modified_by = %(modified_by)s
                WHERE name IN %(names)s AND status = %(active)s
                """,
                {
                    "cleared": CLEARED_STATUS,
                    "modified_by": frappe.session.user or user,
                    "names": names,
                    "active": ACTIVE_STATUS,
                },
            )
            total += len(names)
        return total
