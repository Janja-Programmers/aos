"""Mention helpers for Shorts.

Handles:
- extracting @mentions from captions/comments
- resolving mention handles to users
- syncing AOS Short Mention rows
- notifying mentioned users
"""

from __future__ import annotations

import re
from typing import Any

import frappe

from aos.api.shared.user_display import get_user_display, get_user_display_map
from aos.services.notification_service import NotificationService
from aos.services.shorts.notifications import should_notify
from aos.services.shorts.policy import creator_is_available

MENTION_PATTERN = re.compile(
    r"(?<![\w@.])@([A-Za-z0-9][A-Za-z0-9._+-]{1,79}(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?)"
)


SOURCE_CAPTION = "caption"
SOURCE_COMMENT = "comment"
SOURCE_REPLY = "reply"


def extract_mention_tokens(text: str | None) -> list[str]:
    """Return unique @mention tokens in first-seen order."""
    if not text:
        return []

    seen = set()
    tokens: list[str] = []

    for match in MENTION_PATTERN.finditer(str(text)):
        token = (match.group(1) or "").strip().lower()
        token = token.rstrip(".,;:!?)]}")
        if not token or len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)

    return tokens


def resolve_mention_users(text: str | None) -> list[dict[str, Any]]:
    """Resolve @mention tokens to User records.

    Current AOS profile does not have a dedicated handle field, so resolution is
    intentionally flexible:
    - exact User name/email
    - User username when available
    - email local-part
    - full_name slug fallback
    """
    tokens = extract_mention_tokens(text)
    if not tokens:
        return []

    users = _fetch_candidate_users(tokens)
    resolved: list[dict[str, Any]] = []
    seen_users = set()

    for token in tokens:
        user = users.get(token)
        if not user or user.get("name") in seen_users:
            continue

        seen_users.add(user.get("name"))
        display = get_user_display(user.get("name"))

        resolved.append(
            {
                "token": token,
                "user": display.get("user"),
                "_internal_user": user.get("name"),
                "display_name": display.get("display_name"),
                "avatar": display.get("avatar"),
                "is_deleted": bool(display.get("is_deleted")),
                "is_verified": _is_verified(user.get("name")),
            }
        )

    return resolved


def sync_short_mentions(
    *,
    short_id: str,
    text: str | None,
    mentioned_by: str,
) -> list[dict[str, Any]]:
    """Replace caption mentions for a short."""
    return _sync_mentions(
        short_id=short_id,
        comment_id=None,
        text=text,
        mentioned_by=mentioned_by,
        source_type=SOURCE_CAPTION,
    )


def sync_comment_mentions(
    *,
    short_id: str,
    comment_id: str,
    text: str | None,
    mentioned_by: str,
    is_reply: bool = False,
) -> list[dict[str, Any]]:
    """Replace comment/reply mentions for a comment row."""
    return _sync_mentions(
        short_id=short_id,
        comment_id=comment_id,
        text=text,
        mentioned_by=mentioned_by,
        source_type=SOURCE_REPLY if is_reply else SOURCE_COMMENT,
    )


def delete_comment_mentions(comment_id: str) -> None:
    if not comment_id:
        return

    frappe.db.delete("AOS Short Mention", {"comment": comment_id})


def get_short_mentions_map(short_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not short_ids:
        return {}

    rows = frappe.get_all(
        "AOS Short Mention",
        filters={
            "short": ["in", list({sid for sid in short_ids if sid})],
            "source_type": SOURCE_CAPTION,
        },
        fields=["short", "mentioned_user", "token", "creation"],
        order_by="creation asc",
    )

    user_ids = list({row.mentioned_user for row in rows if row.mentioned_user})
    user_map = get_user_display_map(user_ids) if user_ids else {}
    verified_map = _verified_map(user_ids)

    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row.short, []).append(
            _serialize_mention_row(row.mentioned_user, row.token, user_map, verified_map)
        )

    return result


def get_comment_mentions_map(comment_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not comment_ids:
        return {}

    rows = frappe.get_all(
        "AOS Short Mention",
        filters={"comment": ["in", list({cid for cid in comment_ids if cid})]},
        fields=["comment", "mentioned_user", "token", "creation"],
        order_by="creation asc",
    )

    user_ids = list({row.mentioned_user for row in rows if row.mentioned_user})
    user_map = get_user_display_map(user_ids) if user_ids else {}
    verified_map = _verified_map(user_ids)

    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row.comment, []).append(
            _serialize_mention_row(row.mentioned_user, row.token, user_map, verified_map)
        )

    return result


def _sync_mentions(
    *,
    short_id: str,
    comment_id: str | None,
    text: str | None,
    mentioned_by: str,
    source_type: str,
) -> list[dict[str, Any]]:
    if source_type == SOURCE_CAPTION:
        frappe.db.delete(
            "AOS Short Mention",
            {"short": short_id, "source_type": SOURCE_CAPTION},
        )
    elif comment_id:
        frappe.db.delete("AOS Short Mention", {"comment": comment_id})

    mentions = resolve_mention_users(text)
    for mention in mentions:
        mentioned_user = mention.get("_internal_user")
        if not mentioned_user or not creator_is_available(mentioned_user):
            continue

        mention_doc = frappe.get_doc(
            {
                "doctype": "AOS Short Mention",
                "short": short_id,
                "comment": comment_id,
                "mentioned_user": mentioned_user,
                "mentioned_by": mentioned_by,
                "token": mention.get("token"),
                "source_type": source_type,
            }
        )
        mention_doc.insert(ignore_permissions=True)

        if should_notify(actor=mentioned_by, recipient=mentioned_user):
            try:
                NotificationService.notify_short_mention(
                    user=mentioned_user,
                    actor=mentioned_by,
                    short_id=short_id,
                    comment_id=comment_id,
                    source_type=source_type,
                    event_identity=mention_doc.name,
                )
            except Exception:
                frappe.log_error(
                    "Short mention notification enqueue failed.",
                    "AOS Short Mention Notification",
                )

    return [
        {key: value for key, value in mention.items() if key != "_internal_user"}
        for mention in mentions
        if mention.get("_internal_user") and creator_is_available(mention.get("_internal_user"))
    ]


def _fetch_candidate_users(tokens: list[str]) -> dict[str, dict[str, Any]]:
    if not tokens:
        return {}

    token_set = {token.lower() for token in tokens if token}
    if not token_set:
        return {}

    meta = frappe.get_meta("User")
    fields = ["name", "email", "full_name"]
    if meta.has_field("username"):
        fields.append("username")

    conditions = ["LOWER(name) IN %(tokens)s", "LOWER(email) IN %(tokens)s"]
    if meta.has_field("username"):
        conditions.append("LOWER(username) IN %(tokens)s")

    rows = frappe.db.sql(
        f"""
        SELECT {', '.join(fields)}
        FROM `tabUser`
        WHERE enabled = 1
          AND ({' OR '.join(conditions)})
        """,
        {"tokens": tuple(token_set)},
        as_dict=True,
    )

    # If exact matches were not enough, use a small broad fetch and compare
    # normalized email local-parts/full names in Python. This keeps the SQL safe
    # and avoids depending on database-specific regexp/slug functions.
    if len(rows) < len(token_set):
        extra_rows = frappe.db.sql(
            f"""
            SELECT {', '.join(fields)}
            FROM `tabUser`
            WHERE enabled = 1
            ORDER BY modified DESC
            LIMIT 5000
            """,
            as_dict=True,
        )
        rows.extend(extra_rows or [])

    result: dict[str, dict[str, Any]] = {}

    for row in rows or []:
        aliases = _user_aliases(row)
        for alias in aliases:
            if alias in token_set and alias not in result:
                result[alias] = row

    return result


def _user_aliases(row: dict[str, Any]) -> set[str]:
    aliases = set()

    for key in ("name", "email", "username"):
        value = row.get(key)
        if value:
            value = str(value).strip().lower()
            aliases.add(value)
            if "@" in value:
                aliases.add(value.split("@", 1)[0])

    full_name = row.get("full_name")
    if full_name:
        full_name = str(full_name).strip().lower()
        aliases.add(full_name)
        aliases.add(re.sub(r"[^a-z0-9]+", "", full_name))
        aliases.add(re.sub(r"[^a-z0-9]+", ".", full_name).strip("."))
        aliases.add(re.sub(r"[^a-z0-9]+", "_", full_name).strip("_"))

    return {alias for alias in aliases if alias}


def _is_verified(user: str | None) -> bool:
    if not user:
        return False

    return bool(frappe.db.get_value("AOS Profile", {"user": user}, "is_verified") or 0)


def _verified_map(users: list[str]) -> dict[str, bool]:
    if not users:
        return {}

    rows = frappe.get_all(
        "AOS Profile",
        filters={"user": ["in", list({u for u in users if u})]},
        fields=["user", "is_verified"],
    )
    return {row.user: bool(row.is_verified) for row in rows}


def _serialize_mention_row(
    user: str,
    token: str | None,
    user_map: dict[str, dict[str, Any]],
    verified_map: dict[str, bool],
) -> dict[str, Any]:
    display = user_map.get(user) or get_user_display(user)
    return {
        "user": display.get("user"),
        "token": token,
        "display_name": display.get("display_name"),
        "avatar": display.get("avatar"),
        "is_deleted": bool(display.get("is_deleted")),
        "is_verified": bool(verified_map.get(user)),
    }
