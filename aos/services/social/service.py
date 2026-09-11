"""Canonical Social domain service.

All mutations remain in the caller-managed Frappe transaction. Database unique
constraints are the final concurrency boundary; no service method commits.
"""

from __future__ import annotations

from typing import Any, Callable

import frappe

from aos.api.shared.formatters import humanize_count
from aos.services.notifications.service import NotificationService

from .constants import FOLLOW_NOTIFICATION_DEDUPE_SECONDS
from .errors import SocialPermissionError, SocialValidationError
from .observability import observe
from .policy import SocialPolicy
from .repository import SocialRepository
from .serializers import (
    opaque_block_ref,
    relationship_map,
    relationship_payload,
    serialize_blocked_users,
    serialize_users,
)
from .validation import (
    ensure_known_fields,
    normalize_action,
    normalize_query_aliases,
    normalize_reason,
    normalize_search,
    normalize_target,
    pagination,
    encode_cursor,
)
from .constants import (
    BLOCK_FIELDS,
    BLOCK_LIST_FIELDS,
    FOLLOW_FIELDS,
    LIST_FIELDS,
    RELATIONSHIP_FIELDS,
    SEARCH_FIELDS,
    MAX_SEARCH_LIMIT,
)

ActivityCallback = Callable[..., Any]


class SocialService:
    def __init__(self, repository: SocialRepository | None = None):
        self.repository = repository or SocialRepository()
        self.policy = SocialPolicy(self.repository)

    def toggle_follow(self, *, actor: str, payload: dict[str, Any], activity_callback: ActivityCallback | None = None) -> dict[str, Any]:
        ensure_known_fields(payload, FOLLOW_FIELDS)
        target = normalize_target(payload)
        action = normalize_action(payload.get("action"))
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="follow")
        self.repository.lock_account_pair(user_a=actor, user_b=target)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="follow")
        with observe("toggle_follow") as metric:
            current_relation = self.relationship(actor=actor, target=target, require_available=False)
            if current_relation.get("is_blocked"):
                metric.update(outcome="rejected", reason="blocked")
                raise SocialPermissionError("Social action is unavailable.", code="SOCIAL_BLOCKED")

            existing = self.repository.lock_follow_pair(follower=actor, target=target)
            current = bool(existing)
            desired = (not current) if action == "toggle" else action == "follow"
            changed = False
            notification = "not_requested"

            if desired and not current:
                follow_name, changed = self.repository.insert_follow(follower=actor, target=target)
                if changed:
                    notification = self._notify_follow_atomic(
                        recipient=target, actor=actor, follow_name=follow_name
                    )
                    if activity_callback:
                        activity_callback(user=actor, target_user=target)
            elif not desired and current:
                changed = self.repository.delete_follow(follower=actor, target=target)

            # Reconciliation is deliberately set-based and transaction-local.
            self.repository.sync_counters({actor, target})
            relation = self.relationship(actor=actor, target=target, require_available=False)
            totals = self.repository.profile_totals(viewer=actor, target=target)
            status = "followed" if desired else "unfollowed"
            metric.update(
                outcome="success" if changed else "idempotent",
                changed=changed,
                notification=notification,
            )
            return {
                "status": status,
                "changed": changed,
                **relation,
                **totals,
                "target_total_followers_display": humanize_count(totals["target_total_followers"]),
                "current_total_following_display": humanize_count(totals["current_total_following"]),
            }

    def relationship(self, *, actor: str, target: str, require_available: bool = True) -> dict[str, Any]:
        if actor == target:
            return relationship_payload(
                target=target,
                is_self=True,
                outgoing=False,
                incoming=False,
                blocked_by_me=False,
                blocked_me=False,
            )
        if require_available:
            self.policy.require_actor(actor)
            self.policy.require_target(actor=actor, target=target, operation="inspect")
        mapping = relationship_map(repository=self.repository, viewer=actor, targets=[target])
        return mapping[target]

    def relationship_from_payload(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, RELATIONSHIP_FIELDS)
        target = normalize_target(payload)
        with observe("relationship"):
            return self.relationship(actor=actor, target=target)

    def list_relationships(self, *, actor: str, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        ensure_known_fields(payload, LIST_FIELDS)
        self.policy.require_actor(actor)
        search = normalize_search(payload.get("search"), required=False)
        limit, start, cursor = pagination(payload, kind=mode)
        with observe(f"{mode}_list") as metric:
            rows, total = self.repository.list_social(
                mode=mode,
                viewer=actor,
                limit=limit,
                start=start,
                cursor=cursor,
                search=search,
            )
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = serialize_users(repository=self.repository, viewer=actor, rows=page_rows)
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = encode_cursor(
                    kind=mode,
                    values={"at": str(last.get("sort_at") or ""), "name": str(last.get("edge_name") or "")},
                )
            metric["count"] = len(items)
            return {
                "items": items,
                "total": total,
                "total_display": humanize_count(total),
                "limit": limit,
                "start": start,
                "search": search,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

    def search(self, *, actor: str, payload: dict[str, Any], activity_callback: ActivityCallback | None = None) -> dict[str, Any]:
        ensure_known_fields(payload, SEARCH_FIELDS)
        self.policy.require_actor(actor)
        query = normalize_query_aliases(payload)
        limit, start, cursor = pagination(payload, kind="search", maximum=MAX_SEARCH_LIMIT)
        offset = start
        if cursor is not None:
            expected = self.repository.query_fingerprint(query)
            if cursor.get("q") != expected:
                raise SocialValidationError("Invalid Social pagination cursor.", code="SOCIAL_INVALID_CURSOR")
            try:
                offset = int(cursor.get("offset"))
            except (TypeError, ValueError):
                raise SocialValidationError("Invalid Social pagination cursor.", code="SOCIAL_INVALID_CURSOR") from None
            if offset < 0 or offset > 100_000:
                raise SocialValidationError("Invalid Social pagination cursor.", code="SOCIAL_INVALID_CURSOR")
        with observe("search") as metric:
            rows, total = self.repository.search_users(viewer=actor, query=query, limit=limit, offset=offset)
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            items = serialize_users(repository=self.repository, viewer=actor, rows=page_rows)
            next_cursor = None
            if has_more:
                next_cursor = encode_cursor(
                    kind="search",
                    values={"offset": offset + len(page_rows), "q": self.repository.query_fingerprint(query)},
                )
            if activity_callback:
                activity_callback(user=actor, query=query, result_count=total)
            metric["count"] = len(items)
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "start": offset,
                "query": query,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

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
            block_name, changed = self.repository.activate_block(blocker=actor, blocked=target, reason=reason)
            removed = self.repository.remove_follows_both_directions(user_a=actor, user_b=target)
            self.repository.sync_counters({actor, target})
            if changed and activity_callback:
                activity_callback(user=actor, target_user=target, reason=reason)
            metric.update(outcome="success" if changed or removed else "idempotent", changed=bool(changed or removed))
            return {
                "id": opaque_block_ref(block_name),
                "status": "blocked",
                "changed": bool(changed or removed),
                **self.relationship(actor=actor, target=target, require_available=False),
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
            return {
                "status": "unblocked",
                "changed": changed,
                **self.relationship(actor=actor, target=target, require_available=False),
            }

    def block_status(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, RELATIONSHIP_FIELDS)
        target = normalize_target(payload)
        self.policy.require_actor(actor)
        self.policy.require_target(actor=actor, target=target, operation="inspect", allow_inactive=True)
        with observe("block_status"):
            relation = self.relationship(actor=actor, target=target, require_available=False)
            return {
                key: relation[key]
                for key in (
                    "target_user", "is_blocked_by_me", "has_blocked_me", "is_blocked",
                    "block_status", "can_follow", "can_message", "can_call", "can_view_profile",
                )
            }

    def blocked_users(self, *, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_known_fields(payload, BLOCK_LIST_FIELDS)
        self.policy.require_actor(actor)
        limit, start, cursor = pagination(payload, kind="blocked")
        with observe("blocked_list") as metric:
            rows, total = self.repository.list_blocked(viewer=actor, limit=limit, start=start, cursor=cursor)
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
            return {
                "items": items,
                "total": total,
                "total_display": humanize_count(total),
                "limit": limit,
                "start": start,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

    def _notify_follow_atomic(
        self, *, recipient: str, actor: str, follow_name: str | None = None
    ) -> str:
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
                dedupe_key=(
                    f"social:follow:{follow_name}:{recipient}"
                    if follow_name
                    else None
                ),
            )
        except Exception:
            # Notification is infrastructure. A broken delivery integration must
            # never invalidate the already-authorized Social graph mutation.
            try:
                frappe.log_error(frappe.get_traceback(), "AOS Follow Notification Failed")
            except Exception:
                pass
            return "notification_failed"
        return "outbox_created" if doc else "suppressed_or_disabled"
