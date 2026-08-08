"""Bounded, idempotent reconciliation for the production Chat subsystem.

This patch never commits and never calls external services. It prepares legacy
Chat data for schema-only uniqueness/index installation in install_chat_indexes.
"""

from __future__ import annotations

import hashlib

import frappe

BATCH_SIZE = 250


def _pair_key(participant_1: str, participant_2: str) -> str:
    material = "\x1f".join(sorted([str(participant_1 or ""), str(participant_2 or "")]))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _doctype_exists(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def _normalize_conversations() -> int:
    if not _doctype_exists("AOS Conversation"):
        return 0
    cursor = ""
    changed = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, participant_1, participant_2,
                   unread_count_1, unread_count_2, is_active_1, is_active_2,
                   last_message_1, last_message_at_1, last_sender_1,
                   last_message_2, last_message_at_2, last_sender_2,
                   pair_key
            FROM `tabAOS Conversation`
            WHERE name > %(cursor)s
            ORDER BY name
            LIMIT %(batch)s
            """,
            {"cursor": cursor, "batch": BATCH_SIZE},
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            cursor = str(row.name)
            p1 = str(row.participant_1 or "").strip()
            p2 = str(row.participant_2 or "").strip()
            if not p1 or not p2 or p1 == p2:
                updates = {
                    "pair_key": None,
                    "is_active_1": 0,
                    "is_active_2": 0,
                    "unread_count_1": 0,
                    "unread_count_2": 0,
                }
                frappe.db.set_value("AOS Conversation", row.name, updates, update_modified=False)
                changed += 1
                continue
            low, high = sorted([p1, p2])
            expected = _pair_key(low, high)
            updates = {}
            if (p1, p2) != (low, high):
                updates.update(
                    {
                        "participant_1": low,
                        "participant_2": high,
                        "unread_count_1": max(0, int(row.unread_count_2 or 0)),
                        "unread_count_2": max(0, int(row.unread_count_1 or 0)),
                        "is_active_1": int(bool(row.is_active_2)),
                        "is_active_2": int(bool(row.is_active_1)),
                        "last_message_1": row.last_message_2,
                        "last_message_at_1": row.last_message_at_2,
                        "last_sender_1": row.last_sender_2,
                        "last_message_2": row.last_message_1,
                        "last_message_at_2": row.last_message_at_1,
                        "last_sender_2": row.last_sender_1,
                    }
                )
            else:
                if int(row.unread_count_1 or 0) < 0:
                    updates["unread_count_1"] = 0
                if int(row.unread_count_2 or 0) < 0:
                    updates["unread_count_2"] = 0
            if str(row.pair_key or "") != expected:
                updates["pair_key"] = expected
            if updates:
                frappe.db.set_value("AOS Conversation", row.name, updates, update_modified=False)
                changed += 1
    return changed


def _recompute_unread_counts(conversation_id: str, participant_1: str, participant_2: str) -> None:
    row = frappe.db.sql(
        """
        SELECT
          SUM(CASE WHEN sender = %(p2)s AND read_by_receiver_at IS NULL
                    AND IFNULL(deleted_for_everyone, 0) = 0 AND IFNULL(deleted_for_1, 0) = 0
                   THEN 1 ELSE 0 END) AS unread_1,
          SUM(CASE WHEN sender = %(p1)s AND read_by_receiver_at IS NULL
                    AND IFNULL(deleted_for_everyone, 0) = 0 AND IFNULL(deleted_for_2, 0) = 0
                   THEN 1 ELSE 0 END) AS unread_2
        FROM `tabAOS Message`
        WHERE conversation = %(conversation_id)s
        """,
        {"conversation_id": conversation_id, "p1": participant_1, "p2": participant_2},
        as_dict=True,
    )
    counts = row[0] if row else {}
    frappe.db.set_value(
        "AOS Conversation",
        conversation_id,
        {
            "unread_count_1": max(0, int((counts or {}).get("unread_1") or 0)),
            "unread_count_2": max(0, int((counts or {}).get("unread_2") or 0)),
        },
        update_modified=False,
    )


def _merge_duplicate_conversations() -> int:
    if not _doctype_exists("AOS Conversation"):
        return 0
    merged = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT pair_key
            FROM `tabAOS Conversation`
            WHERE pair_key IS NOT NULL AND pair_key != ''
            GROUP BY pair_key
            HAVING COUNT(*) > 1
            ORDER BY pair_key
            LIMIT 50
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name, participant_1, participant_2, is_active_1, is_active_2
                FROM `tabAOS Conversation`
                WHERE pair_key = %s
                ORDER BY creation ASC, name ASC
                FOR UPDATE
                """,
                (group.pair_key,),
                as_dict=True,
            )
            if len(rows) < 2:
                continue
            keep = rows[0]
            stale = rows[1:]
            keep_active_1 = int(bool(keep.is_active_1) or any(bool(row.is_active_1) for row in stale))
            keep_active_2 = int(bool(keep.is_active_2) or any(bool(row.is_active_2) for row in stale))
            for row in stale:
                stale_id = str(row.name)
                frappe.db.sql(
                    "UPDATE `tabAOS Message` SET conversation = %s WHERE conversation = %s",
                    (keep.name, stale_id),
                )
                frappe.db.sql(
                    "UPDATE `tabAOS Message` SET forwarded_from_conversation = %s WHERE forwarded_from_conversation = %s",
                    (keep.name, stale_id),
                )
                conversation_references = (
                    ("AOS Message Reaction", "UPDATE `tabAOS Message Reaction` SET conversation = %s WHERE conversation = %s"),
                    ("AOS Message Star", "UPDATE `tabAOS Message Star` SET conversation = %s WHERE conversation = %s"),
                    ("AOS Message Translation", "UPDATE `tabAOS Message Translation` SET conversation = %s WHERE conversation = %s"),
                    ("AOS Call", "UPDATE `tabAOS Call` SET conversation = %s WHERE conversation = %s"),
                )
                for doctype, query in conversation_references:
                    if _doctype_exists(doctype) and frappe.db.has_column(doctype, "conversation"):
                        frappe.db.sql(query, (keep.name, stale_id))
                frappe.db.sql("DELETE FROM `tabAOS Conversation` WHERE name = %s", (stale_id,))
                merged += 1
            frappe.db.set_value(
                "AOS Conversation",
                keep.name,
                {"is_active_1": keep_active_1, "is_active_2": keep_active_2},
                update_modified=False,
            )
            _recompute_unread_counts(str(keep.name), str(keep.participant_1), str(keep.participant_2))
            try:
                from aos.api.chat.preview import recompute_conversation_previews

                recompute_conversation_previews(str(keep.name))
            except Exception:
                # Preview reconciliation is derived state. A later Chat interaction
                # repairs it; migration correctness must not depend on serializers.
                frappe.log_error("Chat preview reconciliation deferred.", "Chat migration")
    return merged


def _deactivate_unavailable_participants() -> int:
    """Hide legacy conversation sides owned by missing/disabled/deleted accounts."""
    if not _doctype_exists("AOS Conversation"):
        return 0
    cursor = ""
    changed = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, participant_1, participant_2, is_active_1, is_active_2,
                   unread_count_1, unread_count_2
            FROM `tabAOS Conversation`
            WHERE name > %(cursor)s
            ORDER BY name
            LIMIT %(batch)s
            """,
            {"cursor": cursor, "batch": BATCH_SIZE},
            as_dict=True,
        )
        if not rows:
            break
        participants = sorted({
            str(user) for row in rows for user in (row.participant_1, row.participant_2) if user
        })
        user_rows = frappe.get_all(
            "User",
            filters={"name": ["in", participants]},
            fields=["name", "enabled"],
        ) if participants else []
        user_enabled = {str(row.name): int(row.enabled or 0) == 1 for row in user_rows}
        profile_rows = frappe.get_all(
            "AOS Profile",
            filters={"user": ["in", participants]},
            fields=["user", "account_status", "is_deleted"],
        ) if participants and _doctype_exists("AOS Profile") else []
        profile_map = {str(row.user): row for row in profile_rows}

        def available(user: str | None) -> bool:
            value = str(user or "")
            if not value or not user_enabled.get(value, False):
                return False
            profile = profile_map.get(value)
            if not profile:
                return True
            if bool(int(profile.is_deleted or 0)):
                return False
            status = str(profile.account_status or "Active")
            return status == "Active"

        for row in rows:
            cursor = str(row.name)
            updates = {}
            if not available(row.participant_1):
                if int(row.is_active_1 or 0) != 0:
                    updates["is_active_1"] = 0
                if int(row.unread_count_1 or 0) != 0:
                    updates["unread_count_1"] = 0
            if not available(row.participant_2):
                if int(row.is_active_2 or 0) != 0:
                    updates["is_active_2"] = 0
                if int(row.unread_count_2 or 0) != 0:
                    updates["unread_count_2"] = 0
            if updates:
                frappe.db.set_value("AOS Conversation", row.name, updates, update_modified=False)
                changed += 1
    return changed


def _clear_duplicate_idempotency_keys() -> int:
    if not _doctype_exists("AOS Message"):
        return 0
    cleared = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT idempotency_key
            FROM `tabAOS Message`
            WHERE idempotency_key IS NOT NULL AND idempotency_key != ''
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
            ORDER BY idempotency_key
            LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Message`
                WHERE idempotency_key = %s
                ORDER BY creation ASC, name ASC
                FOR UPDATE
                """,
                (group.idempotency_key,),
                as_dict=True,
            )
            stale = [str(row.name) for row in rows[1:]]
            if stale:
                frappe.db.sql(
                    "UPDATE `tabAOS Message` SET idempotency_key = NULL WHERE name IN %s",
                    (tuple(stale),),
                )
                cleared += len(stale)
    return cleared


def _dedupe_attachments() -> int:
    if (
        not _doctype_exists("AOS Message Attachment")
        or not frappe.db.has_column("AOS Message Attachment", "media")
    ):
        return 0
    removed = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT message, media
            FROM `tabAOS Message Attachment`
            WHERE message IS NOT NULL AND message != ''
              AND media IS NOT NULL AND media != ''
            GROUP BY message, media
            HAVING COUNT(*) > 1
            ORDER BY message, media
            LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS Message Attachment`
                WHERE message = %s AND media = %s
                ORDER BY creation ASC, name ASC
                FOR UPDATE
                """,
                (group.message, group.media),
                pluck=True,
            )
            stale = list(rows[1:])
            if stale:
                frappe.db.delete("AOS Message Attachment", {"name": ["in", stale]})
                removed += len(stale)
    return removed


def _delete_orphan_rows() -> int:
    total = 0
    checks = (
        (
            "AOS Message Reaction",
            """
            SELECT child.name
            FROM `tabAOS Message Reaction` child
            LEFT JOIN `tabAOS Message` message ON message.name = child.message
            WHERE message.name IS NULL
            ORDER BY child.name
            LIMIT %(batch)s
            FOR UPDATE
            """,
        ),
        (
            "AOS Message Star",
            """
            SELECT child.name
            FROM `tabAOS Message Star` child
            LEFT JOIN `tabAOS Message` message ON message.name = child.message
            WHERE message.name IS NULL
            ORDER BY child.name
            LIMIT %(batch)s
            FOR UPDATE
            """,
        ),
        (
            "AOS Message Translation",
            """
            SELECT child.name
            FROM `tabAOS Message Translation` child
            LEFT JOIN `tabAOS Message` message ON message.name = child.message
            WHERE message.name IS NULL
            ORDER BY child.name
            LIMIT %(batch)s
            FOR UPDATE
            """,
        ),
        (
            "AOS Message Attachment",
            """
            SELECT child.name
            FROM `tabAOS Message Attachment` child
            LEFT JOIN `tabAOS Message` message ON message.name = child.message
            WHERE message.name IS NULL
            ORDER BY child.name
            LIMIT %(batch)s
            FOR UPDATE
            """,
        ),
    )
    for doctype, query in checks:
        if not _doctype_exists(doctype):
            continue
        while True:
            rows = frappe.db.sql(query, {"batch": BATCH_SIZE}, as_dict=True)
            names = [str(row.name) for row in rows if row.name]
            if not names:
                break
            frappe.db.delete(doctype, {"name": ["in", names]})
            total += len(names)
    return total


def execute() -> dict[str, int]:
    result = {
        "conversations_normalized": _normalize_conversations(),
        "duplicate_conversations_merged": _merge_duplicate_conversations(),
        "unavailable_participants_deactivated": _deactivate_unavailable_participants(),
        "duplicate_idempotency_keys_cleared": _clear_duplicate_idempotency_keys(),
        "duplicate_attachments_removed": _dedupe_attachments(),
        "orphan_private_rows_removed": _delete_orphan_rows(),
    }
    return result
