"""Canonical persistence boundary for AOS Activity Center.

Owning feature doctypes remain authoritative.  Activity stores a bounded,
private presentation history and never commits or rolls back the caller's
transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import add_days, now_datetime

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.activity.constants import (
    ACTIVE_STATUS,
    ACTIVITY_COUNT_MAX,
    ACTIVITY_DOCTYPE,
    ACTIVITY_RETENTION_DAYS,
    CLEARED_STATUS,
    CLEAR_BATCH_SIZE,
    EVENT_MODE_COALESCE,
    EVENT_MODE_ONCE,
    EVENT_SPECS,
    HIDDEN_STATUS,
    METADATA_JSON_MAX_BYTES,
    METADATA_MAX_KEYS,
    METADATA_TEXT_MAX_LEN,
    RETENTION_DELETE_BATCH_SIZE,
    ROUTE_ID_MAX_LEN,
    ROUTE_TYPE_MAX_LEN,
    TARGET_IMAGE_MAX_LEN,
    TARGET_SUBTITLE_MAX_LEN,
    TARGET_TEXT_MAX_LEN,
    UNIQUE_KEY_MAX_LEN,
)
from aos.services.activity.serializers import serialize_activity
from aos.services.accounts.identity import normalize_public_account_id
from aos.services.sellers.identity import normalize_public_seller_id


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ActivityService:
    """Record, de-duplicate, serialize, hide, clear, and retain Activity rows."""

    @staticmethod
    def _text(value: Any, *, max_len: int) -> str:
        return str(value or "").strip()[:max_len]

    @classmethod
    def _spec(cls, *, activity_group: str, activity_type: str, mode: str) -> dict[str, object]:
        activity_type = cls._text(activity_type, max_len=80)
        spec = EVENT_SPECS.get(activity_type)
        if not spec:
            raise ValueError("Unknown Activity event type")
        if str(spec["group"]) != str(activity_group or "").strip():
            raise ValueError("Activity event group does not match taxonomy")
        if str(spec["mode"]) != mode:
            raise ValueError("Activity event persistence mode does not match taxonomy")
        return spec

    @classmethod
    def _bounded_metadata(cls, *, activity_type: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("Activity metadata must be an object")
        spec = EVENT_SPECS[activity_type]
        schema = dict(spec.get("metadata") or {})
        required = frozenset(spec.get("required_metadata") or ())
        unknown = sorted(str(key) for key in metadata if key not in schema)
        if unknown:
            raise ValueError("Unsupported Activity metadata field")
        missing = sorted(key for key in required if metadata.get(key) in (None, ""))
        if missing:
            raise ValueError("Required Activity metadata field is missing")
        if len(metadata) > METADATA_MAX_KEYS:
            raise ValueError("Activity metadata contains too many fields")

        result: dict[str, Any] = {}
        for key in sorted(metadata):
            raw_value = metadata[key]
            kind = str(schema[key])
            if raw_value is None:
                value = None
            elif kind == "bool":
                if not isinstance(raw_value, bool):
                    raise ValueError("Invalid Activity metadata value")
                value = raw_value
            elif kind == "int":
                if isinstance(raw_value, bool) or not isinstance(raw_value, int) or raw_value < 0:
                    raise ValueError("Invalid Activity metadata value")
                value = raw_value
            elif kind == "number":
                if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, Decimal)):
                    raise ValueError("Invalid Activity metadata value")
                numeric = float(raw_value)
                if not math.isfinite(numeric):
                    raise ValueError("Invalid Activity metadata value")
                value = numeric
            elif kind == "account_id":
                value = normalize_public_account_id(raw_value)
                if not value:
                    raise ValueError("Invalid Activity account identity")
            elif kind == "seller_id":
                value = normalize_public_seller_id(raw_value)
                if not value:
                    raise ValueError("Invalid Activity seller identity")
            elif kind == "text":
                if not isinstance(raw_value, str):
                    raise ValueError("Invalid Activity metadata value")
                value = raw_value.strip()
                if len(value) > METADATA_TEXT_MAX_LEN:
                    raise ValueError("Activity metadata text is too long")
            else:
                raise ValueError("Unsupported Activity metadata schema")

            candidate = {**result, key: value}
            encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > METADATA_JSON_MAX_BYTES:
                raise ValueError("Activity metadata is too large")
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
        """Build a stable hidden dedupe identity from canonical resource inputs."""
        parts = [
            str(activity_type or "").strip(),
            str(target_doctype or "").strip(),
            str(target_name or "").strip(),
            str(route_type or "").strip(),
            str(route_id or "").strip(),
        ]
        material = "|".join(parts).strip("|")
        if not material:
            return ""
        # Persist only a one-way logical identity. Producer inputs may contain
        # internal row/User/report identifiers that must not survive account
        # purge as plaintext merely because a dedupe key is server-hidden.
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_unique_key(value: Any) -> str:
        """Return one canonical hidden SHA-256 logical producer identity."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        if _SHA256_HEX_RE.fullmatch(raw):
            return raw
        return hashlib.sha256(raw[:UNIQUE_KEY_MAX_LEN].encode("utf-8")).hexdigest()

    @staticmethod
    def active_key_for(*, user: str, unique_key: str) -> str | None:
        user = str(user or "").strip()
        unique_key = ActivityService.normalize_unique_key(unique_key)
        if not user or not unique_key:
            return None
        return hashlib.sha256(f"{user}\x1f{unique_key}".encode("utf-8")).hexdigest()

    @staticmethod
    def event_key_for(*, user: str, unique_key: str) -> str | None:
        user = str(user or "").strip()
        unique_key = ActivityService.normalize_unique_key(unique_key)
        if not user or not unique_key:
            return None
        return hashlib.sha256(f"once\x1f{user}\x1f{unique_key}".encode("utf-8")).hexdigest()

    @classmethod
    def _find_active_by_key(cls, active_key: str | None) -> dict[str, Any] | None:
        if not active_key:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT name, public_id
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE active_key = %s AND status = %s
            LIMIT 1
            """,
            (active_key, ACTIVE_STATUS),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    @classmethod
    def _lock_active_by_key(cls, active_key: str | None) -> dict[str, Any] | None:
        if not active_key:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT name, public_id
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE active_key = %s AND status = %s
            LIMIT 1
            FOR UPDATE
            """,
            (active_key, ACTIVE_STATUS),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    @classmethod
    def _find_by_event_key(cls, event_key: str | None) -> dict[str, Any] | None:
        if not event_key:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT name, public_id
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE event_key = %s
            LIMIT 1
            """,
            (event_key,),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    @classmethod
    def _values(
        cls,
        *,
        user: str,
        activity_group: str,
        activity_type: str,
        mode: str,
        target_doctype: str | None,
        target_name: str | None,
        target_title: str | None,
        target_subtitle: str | None,
        target_image: str | None,
        route_type: str | None,
        route_id: str | None,
        metadata: dict[str, Any] | None,
        occurred_at: Any,
        unique_key: str | None,
    ) -> dict[str, Any]:
        user = str(user or "").strip()
        if not user or user == "Guest":
            raise ValueError("Activity owner is required")
        activity_type = cls._text(activity_type, max_len=80)
        spec = cls._spec(activity_group=activity_group, activity_type=activity_type, mode=mode)
        clean_route_type = cls._text(route_type, max_len=ROUTE_TYPE_MAX_LEN)
        clean_route_id = cls._text(route_id, max_len=ROUTE_ID_MAX_LEN)
        clean_target_doctype = cls._text(target_doctype, max_len=TARGET_TEXT_MAX_LEN)
        clean_target_name = cls._text(target_name, max_len=TARGET_TEXT_MAX_LEN)
        if clean_route_type != str(spec["route_type"]):
            raise ValueError("Activity route does not match taxonomy")
        if clean_target_doctype != str(spec["target_doctype"]):
            raise ValueError("Activity target does not match taxonomy")
        if not clean_route_id:
            raise ValueError("Activity public resource identity is required")
        if clean_target_doctype and not clean_target_name:
            raise ValueError("Activity internal resource identity is required")
        clean_unique_key = cls.normalize_unique_key(unique_key)
        if not clean_unique_key:
            raise ValueError("Activity dedupe identity is required")
        timestamp = occurred_at or now_datetime()
        return {
            "doctype": ACTIVITY_DOCTYPE,
            "user": user,
            "activity_group": str(spec["group"]),
            "activity_type": activity_type,
            "status": ACTIVE_STATUS,
            "occurred_at": timestamp,
            "last_occurrence_at": timestamp,
            "count": 1,
            "target_doctype": clean_target_doctype,
            "target_name": clean_target_name,
            "target_title": cls._text(target_title, max_len=TARGET_TEXT_MAX_LEN),
            "target_subtitle": cls._text(target_subtitle, max_len=TARGET_SUBTITLE_MAX_LEN),
            "target_image": cls._text(target_image, max_len=TARGET_IMAGE_MAX_LEN),
            "route_type": clean_route_type,
            "route_id": clean_route_id,
            "metadata_json": cls._bounded_metadata(activity_type=activity_type, metadata=metadata),
            "unique_key": clean_unique_key,
        }

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
    ) -> str | None:
        """Create a one-off event exactly once across producer retries."""
        values = cls._values(
            user=user,
            activity_group=activity_group,
            activity_type=activity_type,
            mode=EVENT_MODE_ONCE,
            target_doctype=target_doctype,
            target_name=target_name,
            target_title=target_title,
            target_subtitle=target_subtitle,
            target_image=target_image,
            route_type=route_type,
            route_id=route_id,
            metadata=metadata,
            occurred_at=occurred_at,
            unique_key=unique_key,
        )
        event_key = cls.event_key_for(user=values["user"], unique_key=values["unique_key"])
        existing = cls._find_by_event_key(event_key)
        if existing:
            return str(existing["public_id"])
        values["event_key"] = event_key
        values["active_key"] = None
        doc = frappe.get_doc(values)
        try:
            doc.insert(ignore_permissions=True)
            return str(doc.public_id)
        except Exception as exc:
            if not event_key or not is_duplicate_entry_error(exc):
                raise
            existing = cls._find_by_event_key(event_key)
            if existing:
                return str(existing["public_id"])
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
    ) -> str | None:
        """Coalesce a repeatable event into one active user/resource row."""
        values = cls._values(
            user=user,
            activity_group=activity_group,
            activity_type=activity_type,
            mode=EVENT_MODE_COALESCE,
            target_doctype=target_doctype,
            target_name=target_name,
            target_title=target_title,
            target_subtitle=target_subtitle,
            target_image=target_image,
            route_type=route_type,
            route_id=route_id,
            metadata=metadata,
            occurred_at=occurred_at,
            unique_key=unique_key,
        )
        active_key = cls.active_key_for(user=values["user"], unique_key=values["unique_key"])
        # Do not take a next-key/gap lock for the common first insert. The DB
        # unique index is the final concurrent insert boundary. Existing rows
        # are locked before their occurrence counter/snapshot is updated.
        existing = cls._find_active_by_key(active_key)
        if existing:
            locked = cls._lock_active_by_key(active_key)
            if locked:
                cls._atomic_update_existing_activity(activity_name=str(locked["name"]), values=values)
                return str(locked["public_id"])

        values["active_key"] = active_key
        values["event_key"] = None
        doc = frappe.get_doc(values)
        try:
            doc.insert(ignore_permissions=True)
            return str(doc.public_id)
        except Exception as exc:
            if not active_key or not is_duplicate_entry_error(exc):
                raise
            existing = cls._lock_active_by_key(active_key)
            if not existing:
                raise
            cls._atomic_update_existing_activity(activity_name=str(existing["name"]), values=values)
            return str(existing["public_id"])

    @classmethod
    def _atomic_update_existing_activity(cls, *, activity_name: str, values: dict[str, Any]) -> None:
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET last_occurrence_at = %s,
                `count` = LEAST(GREATEST(COALESCE(`count`, 0), 1) + 1, %s),
                target_title = %s,
                target_subtitle = %s,
                target_image = %s,
                metadata_json = %s,
                modified = NOW(),
                modified_by = %s
            WHERE name = %s AND status = %s
            """,
            (
                values["last_occurrence_at"],
                ACTIVITY_COUNT_MAX,
                values["target_title"],
                values["target_subtitle"],
                values["target_image"],
                frappe.as_json(values["metadata_json"]),
                getattr(frappe.session, "user", None) or values["user"],
                activity_name,
                ACTIVE_STATUS,
            ),
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
            WHERE public_id = %s AND user = %s
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
            (HIDDEN_STATUS, getattr(frappe.session, "user", None) or user, rows[0].name, user, ACTIVE_STATUS),
        )
        return True

    @classmethod
    def hide_activity_by_unique_key(cls, *, user: str, unique_key: str) -> bool:
        user = str(user or "").strip()
        clean_key = cls.normalize_unique_key(unique_key)
        active_key = cls.active_key_for(user=user, unique_key=clean_key)
        existing = cls._lock_active_by_key(active_key)
        if not existing:
            return False
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET status = %s, active_key = NULL, modified = NOW(), modified_by = %s
            WHERE name = %s AND user = %s AND status = %s
            """,
            (HIDDEN_STATUS, getattr(frappe.session, "user", None) or user, existing["name"], user, ACTIVE_STATUS),
        )
        return True

    @classmethod
    def clear_activity(
        cls,
        *,
        user: str,
        activity_group: str | None = None,
        activity_type: str | None = None,
    ) -> tuple[int, bool]:
        """Clear at most one bounded batch and report whether more rows remain."""
        user = str(user or "").strip()
        if not user:
            return 0, False
        conditions = ["user = %s", "status = %s"]
        params: list[Any] = [user, ACTIVE_STATUS]
        if activity_group:
            conditions.append("activity_group = %s")
            params.append(str(activity_group))
        if activity_type:
            conditions.append("activity_type = %s")
            params.append(str(activity_type))
        where_sql = " AND ".join(conditions)
        rows = frappe.db.sql(
            f"""
            SELECT name
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE {where_sql}
            ORDER BY last_occurrence_at DESC, creation DESC, public_id DESC
            LIMIT %s
            FOR UPDATE
            """,
            [*params, CLEAR_BATCH_SIZE + 1],
            pluck=True,
        )
        names = tuple(str(name) for name in rows[:CLEAR_BATCH_SIZE] if name)
        has_more = len(rows) > CLEAR_BATCH_SIZE
        if not names:
            return 0, False
        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET status = %(cleared)s, active_key = NULL, modified = NOW(), modified_by = %(modified_by)s
            WHERE name IN %(names)s AND user = %(user)s AND status = %(active)s
            """,
            {
                "cleared": CLEARED_STATUS,
                "modified_by": getattr(frappe.session, "user", None) or user,
                "names": names,
                "user": user,
                "active": ACTIVE_STATUS,
            },
        )
        return len(names), has_more

    @classmethod
    def purge_expired(cls, *, limit: int = RETENTION_DELETE_BATCH_SIZE) -> int:
        """Physically remove one bounded batch beyond Activity retention."""
        batch = max(1, min(int(limit or RETENTION_DELETE_BATCH_SIZE), RETENTION_DELETE_BATCH_SIZE))
        cutoff = add_days(now_datetime(), -ACTIVITY_RETENTION_DAYS)
        names = frappe.db.sql(
            f"""
            SELECT name
            FROM `tab{ACTIVITY_DOCTYPE}`
            WHERE last_occurrence_at < %s
            ORDER BY last_occurrence_at ASC, name ASC
            LIMIT %s
            """,
            (cutoff, batch),
            pluck=True,
        )
        clean = tuple(str(name) for name in names if name)
        if not clean:
            return 0
        frappe.db.sql(
            f"DELETE FROM `tab{ACTIVITY_DOCTYPE}` WHERE name IN %(names)s",
            {"names": clean},
        )
        return len(clean)
