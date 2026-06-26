"""User Activity service for Activity Center.

This service records and manages private user-facing history items. Existing
feature doctypes remain the source of truth; AOS User Activity is the compact
Activity Center timeline/history layer.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import get_datetime_str, now_datetime

ACTIVITY_DOCTYPE = "AOS User Activity"
ACTIVE_STATUS = "Active"
HIDDEN_STATUS = "Hidden"
CLEARED_STATUS = "Cleared"

VALID_ACTIVITY_GROUPS = {
    "Shorts",
    "Ads",
    "Search",
    "Social",
    "Live",
    "Reviews",
    "Account",
    "Other",
}


class ActivityService:
    """Record, serialize, hide, and clear Activity Center rows."""

    @staticmethod
    def normalize_group(value: str | None) -> str:
        value = (value or "").strip()
        return value if value in VALID_ACTIVITY_GROUPS else "Other"

    @staticmethod
    def normalize_type(value: str | None) -> str:
        return (value or "").strip()

    @staticmethod
    def normalize_status(value: str | None) -> str:
        value = (value or ACTIVE_STATUS).strip()
        return value if value in {ACTIVE_STATUS, HIDDEN_STATUS, CLEARED_STATUS} else ACTIVE_STATUS

    @staticmethod
    def build_unique_key(
        *,
        activity_type: str,
        target_doctype: str | None = None,
        target_name: str | None = None,
        route_type: str | None = None,
        route_id: str | None = None,
    ) -> str:
        """Build a deterministic key for de-duped activity rows.

        Used by watch/view/search-style activity where repeated actions should
        update one row instead of creating noisy duplicates.
        """
        parts = [
            (activity_type or "").strip(),
            (target_doctype or "").strip(),
            (target_name or "").strip(),
            (route_type or "").strip(),
            (route_id or "").strip(),
        ]

        return "|".join(parts).strip("|")

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
        """Create a new Activity Center row.

        Use this for meaningful one-off events such as comments, reports, ad
        posts, or follows.
        """
        activity_type = cls.normalize_type(activity_type)
        if not user or not activity_type:
            return None

        now = now_datetime()
        occurred_at = occurred_at or now

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
                "target_doctype": (target_doctype or "").strip(),
                "target_name": (target_name or "").strip(),
                "target_title": (target_title or "").strip(),
                "target_subtitle": (target_subtitle or "").strip(),
                "target_image": (target_image or "").strip(),
                "route_type": (route_type or "").strip(),
                "route_id": (route_id or "").strip(),
                "metadata_json": metadata or {},
                "unique_key": (unique_key or "").strip(),
            }
        )
        doc.insert(ignore_permissions=True)
        return doc.name

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
        """Create or update one de-duped Activity Center row.

        Use this for noisy/repeatable actions such as watch history, ad views,
        and search history.
        """
        activity_type = cls.normalize_type(activity_type)
        if not user or not activity_type:
            return None

        unique_key = (unique_key or cls.build_unique_key(
            activity_type=activity_type,
            target_doctype=target_doctype,
            target_name=target_name,
            route_type=route_type,
            route_id=route_id,
        )).strip()

        if unique_key:
            existing = frappe.db.get_value(
                ACTIVITY_DOCTYPE,
                {
                    "user": user,
                    "unique_key": unique_key,
                    "status": ACTIVE_STATUS,
                },
                "name",
                order_by="modified desc",
            )

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

        return cls.record_activity(
            user=user,
            activity_group=activity_group,
            activity_type=activity_type,
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
        """Atomically update an existing de-duped activity row.

        Noisy events such as short watch history can arrive many times in quick
        succession. Loading the document and calling doc.save() can raise
        TimestampMismatchError when two requests update the same activity row at
        the same time. This SQL update keeps the operation atomic and avoids
        failing the user-facing action.
        """
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
            "target_doctype": target_doctype,
            "target_name": target_name,
            "target_title": target_title,
            "target_subtitle": target_subtitle,
            "target_image": target_image,
            "route_type": route_type,
            "route_id": route_id,
        }

        for fieldname, value in optional_fields.items():
            if value is not None:
                set_clauses.append(f"`{fieldname}` = %s")
                params.append((value or "").strip())

        if metadata is not None:
            set_clauses.append("metadata_json = %s")
            params.append(frappe.as_json(metadata or {}))

        params.append(activity_id)

        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET {', '.join(set_clauses)}
            WHERE name = %s
            """,
            params,
        )

    @classmethod
    def _apply_optional_updates(
        cls,
        doc,
        *,
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
        if activity_group:
            doc.activity_group = cls.normalize_group(activity_group)

        optional_fields = {
            "target_doctype": target_doctype,
            "target_name": target_name,
            "target_title": target_title,
            "target_subtitle": target_subtitle,
            "target_image": target_image,
            "route_type": route_type,
            "route_id": route_id,
        }

        for fieldname, value in optional_fields.items():
            if value is not None:
                setattr(doc, fieldname, (value or "").strip())

        if metadata is not None:
            doc.metadata_json = metadata or {}

    @staticmethod
    def serialize_activity(row) -> dict[str, Any]:
        return {
            "id": row.name,
            "activity_group": row.activity_group,
            "activity_type": row.activity_type,
            "status": row.status,
            "target": {
                "doctype": row.target_doctype or None,
                "name": row.target_name or None,
                "title": row.target_title or None,
                "subtitle": row.target_subtitle or None,
                "image": row.target_image or None,
                "route_type": row.route_type or None,
                "route_id": row.route_id or None,
            },
            "metadata": row.metadata_json or {},
            "occurred_at": get_datetime_str(row.occurred_at) if row.occurred_at else None,
            "last_occurrence_at": get_datetime_str(row.last_occurrence_at) if row.last_occurrence_at else None,
            "count": int(row.count or 0),
        }

    @classmethod
    def hide_activity(cls, *, user: str, activity_id: str) -> bool:
        if not user or not activity_id:
            return False

        current_status = frappe.db.get_value(
            ACTIVITY_DOCTYPE,
            {"name": activity_id, "user": user},
            "status",
        )

        if not current_status:
            return False

        if current_status != ACTIVE_STATUS:
            return True

        frappe.db.set_value(
            ACTIVITY_DOCTYPE,
            activity_id,
            "status",
            HIDDEN_STATUS,
            update_modified=True,
        )
        return True



    @classmethod
    def hide_activity_by_unique_key(
        cls,
        *,
        user: str,
        unique_key: str,
    ) -> bool:
        """Hide one active activity row for a user by its deterministic key.

        Useful when a user undoes an action such as unliking a short or when
        the source item is soft-deleted, and we want Activity Center history to
        stop showing that action without deleting the underlying audit/source
        records.
        """
        unique_key = (unique_key or "").strip()

        if not user or not unique_key:
            return False

        activity_id = frappe.db.get_value(
            ACTIVITY_DOCTYPE,
            {
                "user": user,
                "unique_key": unique_key,
                "status": ACTIVE_STATUS,
            },
            "name",
            order_by="modified desc",
        )

        if not activity_id:
            return False

        frappe.db.set_value(
            ACTIVITY_DOCTYPE,
            activity_id,
            "status",
            HIDDEN_STATUS,
            update_modified=True,
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
        """Mark matching active activity rows as cleared for one user."""
        if not user:
            return 0

        filters = {
            "user": user,
            "status": ACTIVE_STATUS,
        }

        if activity_group:
            filters["activity_group"] = cls.normalize_group(activity_group)

        if activity_type:
            filters["activity_type"] = cls.normalize_type(activity_type)

        rows = frappe.get_all(
            ACTIVITY_DOCTYPE,
            filters=filters,
            pluck="name",
        )

        if not rows:
            return 0

        frappe.db.sql(
            f"""
            UPDATE `tab{ACTIVITY_DOCTYPE}`
            SET status = %s,
                modified = NOW(),
                modified_by = %s
            WHERE name IN ({', '.join(['%s'] * len(rows))})
            """,
            [CLEARED_STATUS, frappe.session.user, *rows],
        )

        return len(rows)
