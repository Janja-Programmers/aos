"""Bounded, parameterized persistence for the Social graph."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

import frappe
from frappe.utils import now_datetime

from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.sql_safety import safe_like_prefix

from .constants import BLOCK_ACTIVE, BLOCK_DOCTYPE, BLOCK_UNBLOCKED, FOLLOW_DOCTYPE


class SocialRepository:
    def lock_account_pair(self, *, user_a: str, user_b: str) -> None:
        """Serialize incompatible pair mutations in one deterministic lock order."""
        users = tuple(sorted({str(user_a), str(user_b)}))
        if len(users) != 2:
            return
        frappe.db.sql(
            """
            SELECT name FROM `tabUser`
            WHERE name IN %(users)s
            ORDER BY name ASC
            FOR UPDATE
            """,
            {"users": users},
        )

    def lock_account_pair_shared(self, *, user_a: str, user_b: str) -> None:
        users = tuple(sorted({str(user_a), str(user_b)}))
        if len(users) != 2:
            return
        frappe.db.sql(
            """
            SELECT name FROM `tabUser`
            WHERE name IN %(users)s
            ORDER BY name ASC
            LOCK IN SHARE MODE
            """,
            {"users": users},
        )

    def account_state(self, user: str) -> dict[str, Any] | None:
        rows = frappe.db.sql(
            """
            SELECT u.name AS user, COALESCE(u.enabled, 0) AS enabled,
                   COALESCE(NULLIF(p.account_status, ''), 'Active') AS account_status,
                   CASE WHEN COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Deleted' THEN 1 ELSE 0 END AS is_deleted,
                   p.name AS account_id, p.display_name, p.total_followers, p.total_following,
                   COALESCE(p.total_friends, 0) AS total_friends,
                   COALESCE(p.is_verified, 0) AS is_verified
            FROM `tabUser` u
            INNER JOIN `tabAOS Profile` p ON p.user = u.name
            WHERE u.name = %s
            LIMIT 1
            """,
            (user,),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def follow_exists(self, *, follower: str, target: str) -> bool:
        return bool(frappe.db.exists(FOLLOW_DOCTYPE, {"follower_user": follower, "following_user": target}))

    def lock_follow_pair(self, *, follower: str, target: str) -> dict[str, Any] | None:
        rows = frappe.db.sql(
            """
            SELECT name, creation FROM `tabAOS Follow`
            WHERE follower_user = %s AND following_user = %s
            LIMIT 1 FOR UPDATE
            """,
            (follower, target),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def insert_follow(self, *, follower: str, target: str) -> tuple[str | None, bool]:
        doc = frappe.get_doc({"doctype": FOLLOW_DOCTYPE, "follower_user": follower, "following_user": target})
        try:
            doc.insert(ignore_permissions=True)
            return str(doc.name), True
        except Exception as exc:
            if not is_duplicate_entry_error(exc) and not isinstance(exc, getattr(frappe, "UniqueValidationError", ())):
                raise
            existing = frappe.db.get_value(FOLLOW_DOCTYPE, {"follower_user": follower, "following_user": target}, "name")
            if not existing:
                raise
            return str(existing), False

    def delete_follow(self, *, follower: str, target: str) -> bool:
        existing = self.lock_follow_pair(follower=follower, target=target)
        if not existing:
            return False
        was_friend = self.follow_exists(follower=target, target=follower)
        frappe.db.sql("DELETE FROM `tabAOS Follow` WHERE name = %s", (existing["name"],))
        self.apply_follow_delete_counters(follower=follower, target=target, was_friend=was_friend)
        return True

    def remove_follows_both_directions(self, *, user_a: str, user_b: str) -> int:
        rows = frappe.db.sql(
            """
            SELECT name, follower_user, following_user
            FROM `tabAOS Follow`
            WHERE (follower_user = %s AND following_user = %s)
               OR (follower_user = %s AND following_user = %s)
            ORDER BY name ASC
            FOR UPDATE
            """,
            (user_a, user_b, user_b, user_a),
            as_dict=True,
        )
        if not rows:
            return 0
        names = tuple(str(row.name) for row in rows)
        frappe.db.sql("DELETE FROM `tabAOS Follow` WHERE name IN %(names)s", {"names": names})
        for row in rows:
            self._adjust_profile_counter(str(row.following_user), "total_followers", -1)
            self._adjust_profile_counter(str(row.follower_user), "total_following", -1)
        if len(rows) == 2:
            self._adjust_profile_counter(user_a, "total_friends", -1)
            self._adjust_profile_counter(user_b, "total_friends", -1)
        return len(rows)

    def apply_follow_insert_counters(self, *, follower: str, target: str) -> None:
        self._adjust_profile_counter(target, "total_followers", 1)
        self._adjust_profile_counter(follower, "total_following", 1)
        if self.follow_exists(follower=target, target=follower):
            self._adjust_profile_counter(follower, "total_friends", 1)
            self._adjust_profile_counter(target, "total_friends", 1)

    def apply_follow_delete_counters(self, *, follower: str, target: str, was_friend: bool) -> None:
        self._adjust_profile_counter(target, "total_followers", -1)
        self._adjust_profile_counter(follower, "total_following", -1)
        if was_friend:
            self._adjust_profile_counter(follower, "total_friends", -1)
            self._adjust_profile_counter(target, "total_friends", -1)

    def relationship_sets(
        self,
        *,
        viewer: str,
        targets: Iterable[str],
    ) -> tuple[set[str], set[str], dict[str, tuple[bool, bool]]]:
        unique = sorted({str(target) for target in targets if target and str(target) != viewer})
        if not unique:
            return set(), set(), {}
        outgoing: set[str] = set()
        incoming: set[str] = set()
        block_map = {target: (False, False) for target in unique}
        for index in range(0, len(unique), 200):
            chunk = tuple(unique[index : index + 200])
            follows = frappe.db.sql(
                """
                SELECT follower_user, following_user
                FROM `tabAOS Follow`
                WHERE (follower_user = %(viewer)s AND following_user IN %(targets)s)
                   OR (following_user = %(viewer)s AND follower_user IN %(targets)s)
                """,
                {"viewer": viewer, "targets": chunk},
                as_dict=True,
            )
            outgoing.update(str(row.following_user) for row in follows if str(row.follower_user) == viewer)
            incoming.update(str(row.follower_user) for row in follows if str(row.following_user) == viewer)
            blocks = frappe.db.sql(
                """
                SELECT blocker_user, blocked_user
                FROM `tabAOS User Block`
                WHERE status = %(status)s
                  AND ((blocker_user = %(viewer)s AND blocked_user IN %(targets)s)
                    OR (blocked_user = %(viewer)s AND blocker_user IN %(targets)s))
                """,
                {"status": BLOCK_ACTIVE, "viewer": viewer, "targets": chunk},
                as_dict=True,
            )
            for row in blocks:
                blocker, blocked = str(row.blocker_user), str(row.blocked_user)
                if blocker == viewer and blocked in block_map:
                    block_map[blocked] = (True, block_map[blocked][1])
                elif blocked == viewer and blocker in block_map:
                    block_map[blocker] = (block_map[blocker][0], True)
        return outgoing, incoming, block_map

    def sync_counters(self, users: Iterable[str]) -> None:
        """Rare reconciliation path; normal mutations use constant-time deltas."""
        unique = sorted({str(user) for user in users if user})
        for index in range(0, len(unique), 200):
            chunk = tuple(unique[index : index + 200])
            frappe.db.sql(
                """
                UPDATE `tabAOS Profile` p
                SET p.total_followers = (
                        SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.following_user = p.user
                    ),
                    p.total_following = (
                        SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.follower_user = p.user
                    ),
                    p.total_friends = (
                        SELECT COUNT(*)
                        FROM `tabAOS Follow` outgoing
                        INNER JOIN `tabAOS Follow` incoming
                          ON incoming.follower_user = outgoing.following_user
                         AND incoming.following_user = outgoing.follower_user
                        WHERE outgoing.follower_user = p.user
                    )
                WHERE p.user IN %(users)s
                """,
                {"users": chunk},
            )

    def profile_totals(self, *, viewer: str, target: str) -> dict[str, int]:
        rows = frappe.db.sql(
            """
            SELECT user, total_followers, total_following
            FROM `tabAOS Profile`
            WHERE user IN %(users)s
            """,
            {"users": tuple(sorted({viewer, target}))},
            as_dict=True,
        )
        by_user = {str(row.user): row for row in rows}
        return {
            "target_total_followers": max(0, int(getattr(by_user.get(target), "total_followers", 0) or 0)),
            "current_total_following": max(0, int(getattr(by_user.get(viewer), "total_following", 0) or 0)),
        }

    def lock_block_pair(self, *, blocker: str, blocked: str) -> dict[str, Any] | None:
        rows = frappe.db.sql(
            """
            SELECT name, status, reason, blocked_at, unblocked_at, creation, modified
            FROM `tabAOS User Block`
            WHERE blocker_user = %s AND blocked_user = %s
            LIMIT 1 FOR UPDATE
            """,
            (blocker, blocked),
            as_dict=True,
        )
        return dict(rows[0]) if rows else None

    def activate_block(self, *, blocker: str, blocked: str, reason: str) -> tuple[str, bool]:
        row = self.lock_block_pair(blocker=blocker, blocked=blocked)
        now = now_datetime()
        if row:
            if row.get("status") == BLOCK_ACTIVE:
                if reason != str(row.get("reason") or ""):
                    frappe.db.set_value(BLOCK_DOCTYPE, row["name"], "reason", reason, update_modified=True)
                return str(row["name"]), False
            frappe.db.set_value(
                BLOCK_DOCTYPE,
                row["name"],
                {"status": BLOCK_ACTIVE, "reason": reason, "blocked_at": now, "unblocked_at": None},
                update_modified=True,
            )
            return str(row["name"]), True
        doc = frappe.get_doc({
            "doctype": BLOCK_DOCTYPE,
            "blocker_user": blocker,
            "blocked_user": blocked,
            "status": BLOCK_ACTIVE,
            "reason": reason,
            "blocked_at": now,
        })
        try:
            doc.insert(ignore_permissions=True)
            return str(doc.name), True
        except Exception as exc:
            if not is_duplicate_entry_error(exc) and not isinstance(exc, getattr(frappe, "UniqueValidationError", ())):
                raise
            existing = frappe.db.get_value(BLOCK_DOCTYPE, {"blocker_user": blocker, "blocked_user": blocked}, "name")
            if not existing:
                raise
            return str(existing), False

    def deactivate_block(self, *, blocker: str, blocked: str) -> bool:
        row = self.lock_block_pair(blocker=blocker, blocked=blocked)
        if not row or row.get("status") != BLOCK_ACTIVE:
            return False
        frappe.db.set_value(
            BLOCK_DOCTYPE,
            row["name"],
            {"status": BLOCK_UNBLOCKED, "unblocked_at": now_datetime()},
            update_modified=True,
        )
        return True

    def list_social(self, *, mode: str, viewer: str, limit: int, cursor: dict[str, Any] | None, search: str) -> list[dict[str, Any]]:
        if mode not in {"following", "followers", "friends"}:
            raise ValueError("Unsupported social list mode")
        search_sql, search_params = self._search_filter(search)
        block_sql = """
            AND NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND ((b.blocker_user = %(viewer)s AND b.blocked_user = TARGET_EXPR)
                    OR (b.blocked_user = %(viewer)s AND b.blocker_user = TARGET_EXPR))
            )
        """
        active_sql = "AND u.enabled = 1 AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'"
        params: dict[str, Any] = {"viewer": viewer, "limit": limit + 1, **search_params}
        cursor_sql = ""
        if mode == "following":
            target = "f.following_user"
            if cursor:
                cursor_sql = "AND (f.creation < %(cursor_at)s OR (f.creation = %(cursor_at)s AND f.name < %(cursor_name)s))"
                params.update(cursor_at=cursor.get("at"), cursor_name=cursor.get("name"))
            sql = f"""
                SELECT f.name AS edge_name, f.following_user AS user, f.creation AS followed_at,
                       f.creation AS sort_at, p.total_followers, p.total_following, p.total_friends,
                       p.is_verified, p.display_name
                FROM `tabAOS Follow` f
                INNER JOIN `tabAOS Profile` p ON p.user = f.following_user
                INNER JOIN `tabUser` u ON u.name = f.following_user
                WHERE f.follower_user = %(viewer)s {active_sql}
                  {block_sql.replace('TARGET_EXPR', target)} {search_sql} {cursor_sql}
                ORDER BY f.creation DESC, f.name DESC
                LIMIT %(limit)s
            """
        elif mode == "followers":
            target = "f.follower_user"
            if cursor:
                cursor_sql = "AND (f.creation < %(cursor_at)s OR (f.creation = %(cursor_at)s AND f.name < %(cursor_name)s))"
                params.update(cursor_at=cursor.get("at"), cursor_name=cursor.get("name"))
            sql = f"""
                SELECT f.name AS edge_name, f.follower_user AS user, f.creation AS followed_at,
                       f.creation AS sort_at, p.total_followers, p.total_following, p.total_friends,
                       p.is_verified, p.display_name
                FROM `tabAOS Follow` f
                INNER JOIN `tabAOS Profile` p ON p.user = f.follower_user
                INNER JOIN `tabUser` u ON u.name = f.follower_user
                WHERE f.following_user = %(viewer)s {active_sql}
                  {block_sql.replace('TARGET_EXPR', target)} {search_sql} {cursor_sql}
                ORDER BY f.creation DESC, f.name DESC
                LIMIT %(limit)s
            """
        else:
            target = "f1.following_user"
            if cursor:
                cursor_sql = "AND (f1.creation < %(cursor_at)s OR (f1.creation = %(cursor_at)s AND f1.name < %(cursor_name)s))"
                params.update(cursor_at=cursor.get("at"), cursor_name=cursor.get("name"))
            sql = f"""
                SELECT f1.name AS edge_name, f1.following_user AS user, f1.creation AS followed_at,
                       f2.creation AS followed_back_at, f1.creation AS sort_at,
                       p.total_followers, p.total_following, p.total_friends, p.is_verified, p.display_name
                FROM `tabAOS Follow` f1
                INNER JOIN `tabAOS Follow` f2
                  ON f2.follower_user = f1.following_user AND f2.following_user = f1.follower_user
                INNER JOIN `tabAOS Profile` p ON p.user = f1.following_user
                INNER JOIN `tabUser` u ON u.name = f1.following_user
                WHERE f1.follower_user = %(viewer)s {active_sql}
                  {block_sql.replace('TARGET_EXPR', target)} {search_sql} {cursor_sql}
                ORDER BY f1.creation DESC, f1.name DESC
                LIMIT %(limit)s
            """
        return [dict(row) for row in frappe.db.sql(sql, params, as_dict=True)]

    def search_users(self, *, viewer: str, query: str, limit: int, cursor: dict[str, Any] | None) -> list[dict[str, Any]]:
        prefix = safe_like_prefix(query)
        match_sql = (
            "p.name LIKE %(prefix)s ESCAPE '\\\\'"
            if query.upper().startswith("ACC-")
            else "p.display_name LIKE %(prefix)s ESCAPE '\\\\'"
        )
        params: dict[str, Any] = {"viewer": viewer, "prefix": prefix, "limit": limit + 1}
        cursor_sql = ""
        if cursor:
            cursor_sql = """AND (p.display_name > %(cursor_display)s
                OR (p.display_name = %(cursor_display)s AND p.name > %(cursor_account)s))"""
            params.update(cursor_display=str(cursor.get("display") or ""), cursor_account=str(cursor.get("account_id") or ""))
        rows = frappe.db.sql(
            f"""
            SELECT p.user AS user, p.display_name, p.name AS account_id,
                   p.total_followers, p.total_following, p.total_friends, p.is_verified
            FROM `tabAOS Profile` p
            INNER JOIN `tabUser` u ON u.name = p.user
            WHERE u.enabled = 1
              AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
              AND p.user != %(viewer)s
              AND {match_sql}
              AND NOT EXISTS (
                  SELECT 1 FROM `tabAOS User Block` b
                  WHERE b.status = 'Active'
                    AND ((b.blocker_user = %(viewer)s AND b.blocked_user = p.user)
                      OR (b.blocked_user = %(viewer)s AND b.blocker_user = p.user))
              )
              {cursor_sql}
            ORDER BY p.display_name ASC, p.name ASC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        return [dict(row) for row in rows]

    def list_blocked(self, *, viewer: str, limit: int, cursor: dict[str, Any] | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"viewer": viewer, "status": BLOCK_ACTIVE, "limit": limit + 1}
        cursor_sql = ""
        if cursor:
            cursor_sql = """AND (b.blocked_at < %(cursor_at)s
                OR (b.blocked_at = %(cursor_at)s AND b.name < %(cursor_name)s))"""
            params.update(cursor_at=cursor.get("at"), cursor_name=cursor.get("name"))
        rows = frappe.db.sql(
            f"""
            SELECT b.name AS block_name, b.blocked_user AS user, b.reason,
                   b.blocked_at, b.creation, b.blocked_at AS sort_at
            FROM `tabAOS User Block` b
            WHERE b.blocker_user = %(viewer)s AND b.status = %(status)s {cursor_sql}
            ORDER BY b.blocked_at DESC, b.name DESC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        return [dict(row) for row in rows]

    def list_active_followers_for_event_page(
        self,
        *,
        target: str,
        limit: int,
        after_creation: str | None = None,
        after_name: str | None = None,
    ) -> list[dict[str, str]]:
        bounded = max(1, min(int(limit or 1), 500))
        params: dict[str, object] = {"target": target, "limit": bounded}
        cursor_sql = ""
        if after_creation and after_name:
            params.update({"after_creation": after_creation, "after_name": after_name})
            cursor_sql = """
              AND (f.creation > %(after_creation)s
                   OR (f.creation = %(after_creation)s AND f.name > %(after_name)s))
            """
        rows = frappe.db.sql(
            f"""
            SELECT f.follower_user AS user, f.creation, f.name
            FROM `tabAOS Follow` f
            INNER JOIN `tabUser` u ON u.name = f.follower_user
            INNER JOIN `tabAOS Profile` p ON p.user = f.follower_user
            WHERE f.following_user = %(target)s
              AND u.enabled = 1
              AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
              AND NOT EXISTS (
                  SELECT 1 FROM `tabAOS User Block` b
                  WHERE b.status = 'Active'
                    AND ((b.blocker_user = %(target)s AND b.blocked_user = f.follower_user)
                      OR (b.blocked_user = %(target)s AND b.blocker_user = f.follower_user))
              )
              {cursor_sql}
            ORDER BY f.creation ASC, f.name ASC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        return [{"user": str(row.user), "creation": str(row.creation), "name": str(row.name)} for row in rows if row.user]

    def list_active_followers_for_event(self, *, target: str, limit: int) -> list[str]:
        return [row["user"] for row in self.list_active_followers_for_event_page(target=target, limit=limit)]

    def recent_follow_notification_exists(self, *, recipient: str, actor: str, seconds: int) -> bool:
        return bool(
            frappe.db.sql(
                """
                SELECT 1 FROM `tabAOS Notification`
                WHERE user = %s AND actor = %s AND type = 'follow'
                  AND creation >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                LIMIT 1
                """,
                (recipient, actor, max(1, int(seconds))),
            )
        )

    @staticmethod
    def query_fingerprint(query: str) -> str:
        return hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _search_filter(search: str) -> tuple[str, dict[str, Any]]:
        if not search:
            return "", {}
        field = "p.name" if search.upper().startswith("ACC-") else "p.display_name"
        return (
            f"AND {field} LIKE %(search)s ESCAPE '\\\\'",
            {"search": safe_like_prefix(search)},
        )

    @staticmethod
    def _adjust_profile_counter(user: str, field: str, delta: int) -> None:
        if field not in {"total_followers", "total_following", "total_friends"}:
            raise ValueError("Unsupported Social counter")
        frappe.db.sql(
            f"UPDATE `tabAOS Profile` SET `{field}` = GREATEST(COALESCE(`{field}`, 0) + %s, 0) WHERE user = %s",
            (int(delta), user),
        )
