"""Reconcile legacy Activity Center rows before installing uniqueness indexes.

This post-model-sync patch is intentionally data-only. It preserves user
history, collapses accidental active duplicates into one canonical row, and
redacts legacy metadata that should never have been stored in the private
presentation projection.
"""

from __future__ import annotations

import hashlib
import json

import frappe

from aos.services.accounts.identity import (
    normalize_public_account_id,
    public_account_id_for_user,
)
from aos.services.sellers.identity import (
    normalize_public_seller_id,
    public_seller_id_for_name,
)

_BATCH_SIZE = 250
_REPEATABLE_TYPES = {
    "ad_view",
    "ad_wishlist",
    "ad_posted",
    "short_watch",
    "short_like",
    "short_repost",
    "user_search",
    "user_follow",
    "user_block",
    "live_host",
    "live_join",
}


def execute() -> None:
    if not frappe.db.table_exists("AOS User Activity"):
        return
    _normalize_rows()
    duplicates = _reconcile_active_duplicates()
    _backfill_active_keys()
    redacted = _redact_legacy_private_metadata()
    profile_targets = _canonicalize_profile_targets()
    frappe.logger("aos.activity", allow_site=True).info(
        "activity_data_hardening_complete duplicates_hidden=%s metadata_redacted=%s profile_targets_canonicalized=%s",
        duplicates,
        redacted,
        profile_targets,
    )


def _active_key(user: str, unique_key: str) -> str:
    return hashlib.sha256(f"{str(user or '').strip()}\x1f{str(unique_key or '').strip()}".encode("utf-8")).hexdigest()


def _normalize_rows() -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS User Activity`
        SET status = 'Active'
        WHERE COALESCE(status, '') NOT IN ('Active', 'Hidden', 'Cleared')
        """
    )
    frappe.db.sql(
        """
        UPDATE `tabAOS User Activity`
        SET `count` = 1
        WHERE COALESCE(`count`, 0) < 1
        """
    )
    if frappe.db.has_column("AOS User Activity", "active_key"):
        frappe.db.sql(
            """
            UPDATE `tabAOS User Activity`
            SET active_key = NULL
            WHERE status != 'Active' OR COALESCE(unique_key, '') = ''
            """
        )


def _reconcile_active_duplicates() -> int:
    if not frappe.db.has_column("AOS User Activity", "active_key"):
        return 0
    hidden = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT user, unique_key
            FROM `tabAOS User Activity`
            WHERE status = 'Active'
              AND COALESCE(user, '') != ''
              AND COALESCE(unique_key, '') != ''
            GROUP BY user, unique_key
            HAVING COUNT(*) > 1
            ORDER BY user, unique_key
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name, activity_type, `count`, occurred_at, last_occurrence_at
                FROM `tabAOS User Activity`
                WHERE user = %s AND unique_key = %s AND status = 'Active'
                ORDER BY COALESCE(last_occurrence_at, occurred_at, creation) DESC, creation DESC, name DESC
                """,
                (group.user, group.unique_key),
                as_dict=True,
            )
            if len(rows) <= 1:
                continue
            keeper = rows[0]
            duplicate_names = tuple(str(row.name) for row in rows[1:] if row.name)
            if not duplicate_names:
                continue
            if str(keeper.activity_type or "") in _REPEATABLE_TYPES:
                merged_count = sum(max(int(row.count or 0), 1) for row in rows)
            else:
                merged_count = max(max(int(row.count or 0), 1) for row in rows)
            occurred_values = [row.occurred_at for row in rows if row.occurred_at]
            last_values = [row.last_occurrence_at or row.occurred_at for row in rows if row.last_occurrence_at or row.occurred_at]
            frappe.db.set_value(
                "AOS User Activity",
                keeper.name,
                {
                    "count": merged_count,
                    "occurred_at": min(occurred_values) if occurred_values else keeper.occurred_at,
                    "last_occurrence_at": max(last_values) if last_values else keeper.last_occurrence_at,
                },
                update_modified=False,
            )
            frappe.db.sql(
                """
                UPDATE `tabAOS User Activity`
                SET status = 'Hidden', active_key = NULL
                WHERE name IN %(names)s
                """,
                {"names": duplicate_names},
            )
            hidden += len(duplicate_names)
    return hidden


def _backfill_active_keys() -> None:
    if not frappe.db.has_column("AOS User Activity", "active_key"):
        return
    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, user, unique_key
            FROM `tabAOS User Activity`
            WHERE name > %s
              AND status = 'Active'
              AND COALESCE(user, '') != ''
              AND COALESCE(unique_key, '') != ''
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            frappe.db.set_value(
                "AOS User Activity",
                row.name,
                "active_key",
                _active_key(row.user, row.unique_key),
                update_modified=False,
            )
        start_after = rows[-1].name


def _redact_legacy_private_metadata() -> int:
    redacted = 0
    start_after = ""
    forbidden = {"seller_user", "session_id", "view_id", "report_id"}
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, metadata_json
            FROM `tabAOS User Activity`
            WHERE name > %s AND COALESCE(metadata_json, '') != ''
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            raw = row.metadata_json
            if isinstance(raw, dict):
                metadata = dict(raw)
            else:
                try:
                    metadata = json.loads(raw or "{}")
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            changed = False
            for key in forbidden:
                if key in metadata:
                    metadata.pop(key, None)
                    changed = True

            for key in ("short_owner", "host_user", "target_user"):
                value = metadata.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                raw_value = value.strip()
                public_id = normalize_public_account_id(raw_value)
                if not public_id:
                    try:
                        public_id = public_account_id_for_user(raw_value) or ""
                    except Exception:
                        public_id = ""
                if public_id and public_id != value:
                    metadata[key] = public_id
                    changed = True
                elif not public_id:
                    metadata.pop(key, None)
                    changed = True

            seller = metadata.get("seller")
            if isinstance(seller, str) and seller.strip():
                raw_seller = seller.strip()
                public_seller = normalize_public_seller_id(raw_seller)
                if not public_seller:
                    try:
                        public_seller = public_seller_id_for_name(raw_seller) or ""
                    except Exception:
                        public_seller = ""
                if public_seller and public_seller != seller:
                    metadata["seller"] = public_seller
                    changed = True
                elif not public_seller:
                    metadata.pop("seller", None)
                    changed = True

            if changed:
                frappe.db.set_value(
                    "AOS User Activity",
                    row.name,
                    "metadata_json",
                    frappe.as_json(metadata),
                    update_modified=False,
                )
                redacted += 1
        start_after = rows[-1].name
    return redacted


def _canonicalize_profile_targets() -> int:
    # route_id was already the public ACC-* identifier in the existing producer.
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS User Activity`
        WHERE route_type = 'profile'
          AND route_id LIKE 'ACC-%'
          AND COALESCE(target_name, '') != route_id
        ORDER BY name
        """,
        pluck=True,
    )
    if not rows:
        return 0
    names = tuple(str(name) for name in rows if name)
    frappe.db.sql(
        """
        UPDATE `tabAOS User Activity`
        SET target_name = route_id
        WHERE name IN %(names)s
        """,
        {"names": names},
    )
    return len(names)
