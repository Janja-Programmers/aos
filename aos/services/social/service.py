"""Canonical Social application service.

Social mutations remain inside the caller-owned Frappe transaction. Pair locks
serialize incompatible relationship changes; database uniqueness is the final
retry/concurrency boundary.
"""

from __future__ import annotations

from typing import Any, Callable

import frappe

from aos.api.shared.formatters import humanize_count
from aos.services.notifications.service import NotificationService

from .capabilities import SocialCapabilityService
from .constants import (
    BLOCK_FIELDS,
    BLOCK_LIST_FIELDS,
    FOLLOW_FIELDS,
    FOLLOW_NOTIFICATION_DEDUPE_SECONDS,
    LIST_FIELDS,
    MAX_SEARCH_LIMIT,
    RELATIONSHIP_FIELDS,
    SEARCH_FIELDS,
)
from .errors import SocialNotFoundError, SocialPermissionError, SocialValidationError
from .observability import observe
from .policy import SocialPolicy
from .repository import SocialRepository
from .serializers import opaque_block_ref, serialize_blocked_users, serialize_users
from .validation import encode_cursor, ensure_known_fields, normalize_reason, normalize_search, normalize_target, pagination

ActivityCallback = Callable[..., Any]


class SocialService:
    def __init__(self, repository: SocialRepository | None = None):
        self.repository = repository or SocialRepository()
        self.policy = SocialPolicy(self.repository)
        self.capabilities = SocialCapabilityService(self.repository)

    def follow(
        self,
        *,
        actor: str,
        payload: dict[str, Any],
        activity_callback: ActivityCallback | None = None,
    ) -> dict[str, Any]:
        return self._set_follow(actor=actor, payload=payload, desired=True, activity_callback=activity_callback)

    def unfollow(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._set_follow(actor=actor, payload=payload, desired=False, activity_callback=None)

    def _set_follow(
        self,
        *,
        actor: str,
        payload: dict[str, Any],
        desired: bool,
        activity_callback: ActivityCallback | None,
    ) -> dict[str, Any]:
        ensure_known_fields(payload, FOLLOW_FIELDS)
        target = normalize_target(payload)
        operation = "follow" if desired else "unfollow"
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation=operation, allow_inactive=not desired)
        self.repository.lock_account_pair(user_a=actor, user_b=target)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation=operation, allow_inactive=not desired)
        with observe(operation) as metric:
            relation = self.relationship(actor=actor, target=target, require_available=False)
            if desired and relation.get("is_blocked_by_me"):
                metric.update(outcome="rejected", reason="blocked")
                raise SocialPermissionError("Social action is unavailable.", code="SOCIAL_BLOCKED")
            if desired and relation.get("has_blocked_me"):
                metric.update(outcome="rejected", reason="unavailable")
                raise SocialNotFoundError("Profile unavailable.")

            notification = "not_requested"
            if desired:
                follow_name, changed = self.repository.insert_follow(follower=actor, target=target)
                if changed:
                    notification = self._notify_follow_atomic(recipient=target, actor=actor, follow_name=follow_name)
                    if activity_callback:
                        activity_callback(user=actor, target_user=target)
            else:
                changed = self.repository.delete_follow(follower=actor, target=target)

            relation = self._public_relationship_projection(
                self.relationship(actor=actor, target=target, require_available=False),
                reject_incoming_only=False,
            )
            totals = self.repository.profile_totals(viewer=actor, target=target)
            metric.update(outcome="success" if changed else "idempotent", changed=changed, notification=notification)
            return {
                "status": "followed" if desired else "unfollowed",
                "changed": changed,
                **relation,
                **totals,
                "target_total_followers_display": humanize_count(totals["target_total_followers"]),
                "current_total_following_display": humanize_count(totals["current_total_following"]),
            }

    def relationship(self, *, actor: str, target: str, require_available: bool = True) -> dict[str, Any]:
        if require_available:
            self.policy.require_actor(actor)
            self.policy.require_target(actor=actor, target=target, operation="inspect")
        return self.capabilities.relationship_projection(viewer=actor, target=target)

    def relationship_from_payload(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, RELATIONSHIP_FIELDS)
        target = normalize_target(payload)
        with observe("relationship"):
            relation = self.relationship(actor=actor, target=target)
            return self._public_relationship_projection(relation, reject_incoming_only=True)

    def list_relationships(self, *, actor: str, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        ensure_known_fields(payload, LIST_FIELDS)
        self.policy.require_actor(actor)
        search = normalize_search(payload.get("search"), required=False)
        limit, cursor = pagination(payload, kind=mode)
        search_fingerprint = self.repository.query_fingerprint(search) if search else ""
        if cursor is not None and str(cursor.get("q") or "") != search_fingerprint:
            raise SocialValidationError("Invalid Social pagination cursor.", code="SOCIAL_INVALID_CURSOR")
        with observe(f"{mode}_list") as metric:
            rows = self.repository.list_social(mode=mode, viewer=actor, limit=limit, cursor=cursor, search=search)
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = serialize_users(repository=self.repository, viewer=actor, rows=page_rows)
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = encode_cursor(
                    kind=mode,
                    values={
                        "at": str(last.get("sort_at") or ""),
                        "name": str(last.get("edge_name") or ""),
                        "q": search_fingerprint,
                    },
                )
            metric["count"] = len(items)
            return {
                "items": items,
                "limit": limit,
                "search": search,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

    def search(self, *, actor: str, payload: dict[str, Any], activity_callback: ActivityCallback | None = None) -> dict[str, Any]:
        ensure_known_fields(payload, SEARCH_FIELDS)
        self.policy.require_actor(actor)
        query = normalize_search(payload.get("query"), required=True)
        limit, cursor = pagination(payload, kind="search", maximum=MAX_SEARCH_LIMIT)
        if cursor is not None and cursor.get("q") != self.repository.query_fingerprint(query):
            raise SocialValidationError("Invalid Social pagination cursor.", code="SOCIAL_INVALID_CURSOR")
        with observe("search") as metric:
            rows = self.repository.search_users(viewer=actor, query=query, limit=limit, cursor=cursor)
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = serialize_users(repository=self.repository, viewer=actor, rows=page_rows)
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = encode_cursor(
                    kind="search",
                    values={
                        "display": str(last.get("display_name") or ""),
                        "account_id": str(last.get("account_id") or ""),
                        "q": self.repository.query_fingerprint(query),
                    },
                )
            if activity_callback:
                activity_callback(user=actor, query=query, result_count=len(items))
            metric["count"] = len(items)
            return {"items": items, "limit": limit, "query": query, "has_more": has_more, "next_cursor": next_cursor}

    def block(self, *, actor: str, payload: dict[str, Any], activity_callback: ActivityCallback | None = None) -> dict[str, Any]:
        ensure_known_fields(payload, BLOCK_FIELDS)
        target = normalize_target(payload)
        reason = normalize_reason(payload.get("reason"))
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="block")
        self.repository.lock_account_pair(user_a=actor, user_b=target)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="block")
        with observe("block") as metric:
            block_name, block_changed = self.repository.activate_block(blocker=actor, blocked=target, reason=reason)
            removed = self.repository.remove_follows_both_directions(user_a=actor, user_b=target)
            changed = bool(block_changed or removed)
            if block_changed and activity_callback:
                activity_callback(user=actor, target_user=target, reason=reason)
            metric.update(outcome="success" if changed else "idempotent", changed=changed)
            relation = self._public_relationship_projection(
                self.relationship(actor=actor, target=target, require_available=False),
                reject_incoming_only=False,
            )
            return {
                "id": opaque_block_ref(block_name),
                "status": "blocked",
                "changed": changed,
                **relation,
            }

    def unblock(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, RELATIONSHIP_FIELDS)
        target = normalize_target(payload)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="unblock", allow_inactive=True)
        self.repository.lock_account_pair(user_a=actor, user_b=target)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="unblock", allow_inactive=True)
        with observe("unblock") as metric:
            changed = self.repository.deactivate_block(blocker=actor, blocked=target)
            metric.update(outcome="success" if changed else "idempotent", changed=changed)
            relation = self._public_relationship_projection(
                self.relationship(actor=actor, target=target, require_available=False),
                reject_incoming_only=False,
            )
            return {
                "status": "unblocked",
                "changed": changed,
                **relation,
            }

    def block_status(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, RELATIONSHIP_FIELDS)
        target = normalize_target(payload)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="inspect", allow_inactive=True)
        with observe("block_status"):
            relation = self._public_relationship_projection(
                self.relationship(actor=actor, target=target, require_available=False),
                reject_incoming_only=True,
            )
            return {
                key: relation[key]
                for key in (
                    "account_id", "is_blocked_by_me", "has_blocked_me", "is_blocked",
                    "block_status", "can_follow", "can_message", "can_call", "can_view_profile",
                )
            }

    def blocked_users(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, BLOCK_LIST_FIELDS)
        self.policy.require_actor(actor)
        limit, cursor = pagination(payload, kind="blocked")
        with observe("blocked_list") as metric:
            rows = self.repository.list_blocked(viewer=actor, limit=limit, cursor=cursor)
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = serialize_blocked_users(page_rows)
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = encode_cursor(
                    kind="blocked",
                    values={"at": str(last.get("sort_at") or ""), "name": str(last.get("block_name") or "")},
                )
            metric["count"] = len(items)
            return {"items": items, "limit": limit, "has_more": has_more, "next_cursor": next_cursor}

    @staticmethod
    def _public_relationship_projection(
        relation: dict[str, Any],
        *,
        reject_incoming_only: bool,
    ) -> dict[str, Any]:
        """Return a client-safe projection without revealing incoming block direction.

        Internal consumers need both block directions to enforce policy, but the
        public Social surface must not let a client discover that another account
        blocked them. If the viewer also owns a block, only that viewer-owned
        block is exposed. Mutation responses that must remain successful after a
        state change return a neutral ``Unavailable`` projection for incoming-only
        blocks instead of rolling the mutation back.
        """

        public = dict(relation)
        blocked_by_me = bool(public.get("is_blocked_by_me"))
        blocked_me = bool(public.get("has_blocked_me"))
        if blocked_me and not blocked_by_me:
            if reject_incoming_only:
                raise SocialNotFoundError("Profile unavailable.")
            public.update({
                "is_following": False,
                "is_followed_by": False,
                "is_friend": False,
                "relationship_status": "none",
                "action_label": "Unavailable",
                "is_blocked_by_me": False,
                "has_blocked_me": False,
                "is_blocked": False,
                "block_status": "none",
                "can_follow": False,
                "can_message": False,
                "can_call": False,
                "can_view_profile": False,
            })
            return public
        if blocked_by_me:
            public.update({
                "has_blocked_me": False,
                "is_blocked": True,
                "block_status": "blocked_by_me",
            })
        return public

    def _notify_follow_atomic(self, *, recipient: str, actor: str, follow_name: str | None = None) -> str:
        if self.repository.recent_follow_notification_exists(
            recipient=recipient,
            actor=actor,
            seconds=FOLLOW_NOTIFICATION_DEDUPE_SECONDS,
        ):
            return "deduplicated"
        try:
            doc = NotificationService.notify_follow(
                user=recipient,
                follower=actor,
                dedupe_key=f"social:follow:{follow_name}:{recipient}" if follow_name else None,
            )
        except Exception:
            try:
                frappe.log_error("Follow notification creation failed.", "AOS Follow Notification Failed")
            except Exception:
                pass
            return "notification_failed"
        return "outbox_created" if doc else "suppressed_or_disabled"
