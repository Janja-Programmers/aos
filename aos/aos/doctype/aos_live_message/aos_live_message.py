# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document

from aos.services.live_analytics_service import LiveAnalyticsService


LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"
USER_DOCTYPE = "User"

ACTIVE_MESSAGE_STATUS = "active"
LIVE_STATUS = "live"

COMMENT_KIND = "comment"
COMMENT_TYPE = "comment"
REPLY_TYPE = "reply"

VALID_MESSAGE_STATUSES = {
    "active",
    "hidden",
    "deleted",
}

VALID_MESSAGE_KINDS = {
    "comment",
    "system",
    "cohost",
    "gift",
    "moderation",
}

MESSAGE_TYPES_BY_KIND = {
    "comment": {
        "comment",
        "reply",
    },
    "system": {
        "live_started",
        "notifying_followers",
        "viewer_joined",
        "live_ended",
        "generic",
    },
    "cohost": {
        "cohost_invited",
        "cohost_request_sent",
        "cohost_request_accepted",
        "cohost_request_rejected",
        "cohost_started",
        "cohost_ended",
        "generic",
    },
    "gift": {
        "generic",
    },
    "moderation": {
        "generic",
    },
}

MESSAGE_TYPES_ALLOWED_WHEN_LIVE_INACTIVE = {
    "live_ended",
    "cohost_ended",
    "generic",
}


class AOSLiveMessage(Document):
    def validate(self):
        self._normalize_values()
        self._validate_required_fields()
        self._validate_message_kind_and_type()
        self._validate_status()
        self._validate_user()
        self._validate_target_user()
        self._validate_live_stream()
        self._validate_parent_message()
        self._validate_metadata()
        self._normalize_visibility()

    def before_insert(self):
        self._set_root_message()

    def after_insert(self):
        self._increment_reply_count()
        self._increment_live_comment_count()

    def on_update(self):
        """
        Synchronize derived counters when an existing message is updated.

        This covers changes made through:
        - Desk
        - internal document saves
        - moderation services using doc.save()

        Direct SQL updates still need to synchronize counters explicitly.
        """

        previous = self.get_doc_before_save()

        if not previous:
            return

        status_changed = previous.status != self.status

        classification_changed = (
            previous.message_kind != self.message_kind
            or previous.message_type != self.message_type
        )

        parent_changed = (
            previous.parent_message != self.parent_message
        )

        live_changed = (
            previous.live_stream != self.live_stream
        )

        if not any(
            {
                status_changed,
                classification_changed,
                parent_changed,
                live_changed,
            }
        ):
            return

        affected_live_ids = {
            previous.live_stream,
            self.live_stream,
        }

        for live_id in affected_live_ids:
            if not live_id:
                continue

            LiveAnalyticsService.sync_comment_count(
                live_id=live_id,
            )

        affected_parent_ids = {
            previous.parent_message,
            self.parent_message,
        }

        for parent_id in affected_parent_ids:
            self._sync_parent_reply_count(
                parent_id
            )

    def on_trash(self):
        self._decrement_reply_count()
        self._decrement_live_comment_count()

    # NORMALIZATION
    def _normalize_values(self):
        self.message_kind = str(
            self.message_kind or COMMENT_KIND
        ).strip().lower()

        self.message_type = str(
            self.message_type or COMMENT_TYPE
        ).strip().lower()

        self.status = str(
            self.status or ACTIVE_MESSAGE_STATUS
        ).strip().lower()

        if self.content is not None:
            self.content = str(
                self.content
            ).strip()

    def _normalize_visibility(self):
        """
        Apply visibility rules that must not depend on API input.
        """

        if self.message_kind == COMMENT_KIND:
            self.visible_to_host = 1
            self.visible_to_viewers = 1
            return

        if self.message_type == "notifying_followers":
            self.visible_to_host = 1
            self.visible_to_viewers = 0
            return

        if self.message_type == "viewer_joined":
            self.visible_to_host = 1
            self.visible_to_viewers = 0

    # VALIDATIONS
    def _validate_required_fields(self):
        if not self.live_stream:
            frappe.throw(
                "Live stream is required."
            )

        if not self.message_kind:
            frappe.throw(
                "Message kind is required."
            )

        if not self.message_type:
            frappe.throw(
                "Message type is required."
            )

        if not self.content:
            frappe.throw(
                "Message content is required."
            )

    def _validate_message_kind_and_type(self):
        if self.message_kind not in VALID_MESSAGE_KINDS:
            frappe.throw(
                f"Invalid message kind: "
                f"{self.message_kind}."
            )

        valid_types = MESSAGE_TYPES_BY_KIND.get(
            self.message_kind,
            set(),
        )

        if self.message_type not in valid_types:
            frappe.throw(
                f"Message type '{self.message_type}' "
                f"is not valid for message kind "
                f"'{self.message_kind}'."
            )

    def _validate_status(self):
        if self.status not in VALID_MESSAGE_STATUSES:
            frappe.throw(
                f"Invalid message status: "
                f"{self.status}."
            )

    def _validate_user(self):
        """
        Comments and replies require an enabled author.

        System, co-host, gift, and moderation messages may exist without an
        actor user.
        """

        if (
            self.message_kind == COMMENT_KIND
            and not self.user
        ):
            frappe.throw(
                "User is required for comments and replies."
            )

        if not self.user:
            return

        user = frappe.db.get_value(
            USER_DOCTYPE,
            self.user,
            [
                "name",
                "enabled",
            ],
            as_dict=True,
        )

        if not user:
            frappe.throw(
                "Invalid user."
            )

        if (
            self.message_kind == COMMENT_KIND
            and not bool(user.enabled)
        ):
            frappe.throw(
                "User account is disabled."
            )

    def _validate_target_user(self):
        if not self.target_user:
            return

        if not frappe.db.exists(
            USER_DOCTYPE,
            self.target_user,
        ):
            frappe.throw(
                "Invalid target user."
            )

    def _validate_live_stream(self):
        live = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            self.live_stream,
            [
                "name",
                "status",
                "is_active",
            ],
            as_dict=True,
        )

        if not live:
            frappe.throw(
                "Invalid live stream."
            )

        # Existing messages may still be hidden, deleted, restored, or
        # otherwise moderated after the live ends.
        if not self.is_new():
            return

        is_live = (
            live.status == LIVE_STATUS
            and bool(live.is_active)
        )

        if is_live:
            return

        if (
            self.message_type
            in MESSAGE_TYPES_ALLOWED_WHEN_LIVE_INACTIVE
        ):
            return

        frappe.throw(
            "Cannot create this message on an inactive live stream."
        )

    def _validate_parent_message(self):
        if self.message_kind != COMMENT_KIND:
            if self.parent_message:
                frappe.throw(
                    "Only comments can have a parent message."
                )

            if self.root_message:
                frappe.throw(
                    "Only comment replies can have a root message."
                )

            return

        if self.message_type == COMMENT_TYPE:
            if self.parent_message:
                frappe.throw(
                    "A top-level comment cannot have a parent message."
                )

            return

        if self.message_type != REPLY_TYPE:
            return

        if not self.parent_message:
            frappe.throw(
                "Parent message is required for a reply."
            )

        if self.parent_message == self.name:
            frappe.throw(
                "A message cannot be its own parent."
            )

        parent = frappe.db.get_value(
            LIVE_MESSAGE_DOCTYPE,
            self.parent_message,
            [
                "name",
                "live_stream",
                "message_kind",
                "message_type",
                "status",
                "root_message",
            ],
            as_dict=True,
        )

        if not parent:
            frappe.throw(
                "Invalid parent message."
            )

        if parent.live_stream != self.live_stream:
            frappe.throw(
                "Parent message must belong to the same live stream."
            )

        if parent.message_kind != COMMENT_KIND:
            frappe.throw(
                "Replies can only target comment messages."
            )

        if parent.status != ACTIVE_MESSAGE_STATUS:
            frappe.throw(
                "Cannot reply to an inactive message."
            )

    def _validate_metadata(self):
        if self.metadata_json in (
            None,
            "",
            {},
        ):
            self.metadata_json = None
            return

        try:
            metadata = frappe.parse_json(
                self.metadata_json
            )
        except Exception:
            frappe.throw(
                "Metadata JSON must contain valid JSON."
            )

        if not isinstance(metadata, dict):
            frappe.throw(
                "Metadata JSON must contain a JSON object."
            )

        self.metadata_json = frappe.as_json(
            metadata
        )

    # SETTERS
    def _set_root_message(self):
        """
        Calculate root_message internally.

        Top-level comments have no root message. Replies point to the original
        top-level message in their thread.
        """

        if self.message_kind != COMMENT_KIND:
            self.parent_message = None
            self.root_message = None
            return

        if self.message_type == COMMENT_TYPE:
            self.parent_message = None
            self.root_message = None
            return

        if self.message_type != REPLY_TYPE:
            self.root_message = None
            return

        parent = frappe.db.get_value(
            LIVE_MESSAGE_DOCTYPE,
            self.parent_message,
            [
                "name",
                "root_message",
            ],
            as_dict=True,
        )

        if not parent:
            frappe.throw(
                "Invalid parent message."
            )

        self.root_message = (
            parent.root_message
            or parent.name
        )

    # SIDE EFFECTS
    def _increment_reply_count(self):
        if not self._should_affect_reply_count():
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Message`
            SET reply_count = COALESCE(reply_count, 0) + 1
            WHERE name = %s
            """,
            (self.parent_message,),
        )

    def _decrement_reply_count(self):
        """
        Decrement only when this reply was actively represented in the
        parent's reply_count.

        Hidden or already-deleted replies must not decrement again.
        """

        if not self._should_affect_reply_count():
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Message`
            SET reply_count = GREATEST(
                COALESCE(reply_count, 0) - 1,
                0
            )
            WHERE name = %s
            """,
            (self.parent_message,),
        )

    def _increment_live_comment_count(self):
        if not self._should_affect_comment_count():
            return

        LiveAnalyticsService.handle_comment_added(
            live_id=self.live_stream,
        )

    def _decrement_live_comment_count(self):
        if not self._should_affect_comment_count():
            return

        LiveAnalyticsService.handle_comment_deleted(
            live_id=self.live_stream,
        )

    def _sync_parent_reply_count(
        self,
        parent_id: str | None,
    ):
        if not parent_id:
            return

        if not frappe.db.exists(
            LIVE_MESSAGE_DOCTYPE,
            parent_id,
        ):
            return

        reply_count = frappe.db.count(
            LIVE_MESSAGE_DOCTYPE,
            filters={
                "parent_message": parent_id,
                "message_kind": COMMENT_KIND,
                "message_type": REPLY_TYPE,
                "status": ACTIVE_MESSAGE_STATUS,
            },
        )

        frappe.db.set_value(
            LIVE_MESSAGE_DOCTYPE,
            parent_id,
            "reply_count",
            int(reply_count or 0),
            update_modified=False,
        )

    # HELPERS
    def _is_reply(self) -> bool:
        return bool(
            self.message_kind == COMMENT_KIND
            and self.message_type == REPLY_TYPE
            and self.parent_message
        )

    def _should_affect_reply_count(self) -> bool:
        return bool(
            self._is_reply()
            and self.status == ACTIVE_MESSAGE_STATUS
        )

    def _should_affect_comment_count(self) -> bool:
        """
        Only active comments and replies affect comment analytics.

        System, co-host, gift, moderation, hidden, and deleted messages never
        affect the live stream's comment count.
        """

        return bool(
            self.message_kind == COMMENT_KIND
            and self.message_type in {
                COMMENT_TYPE,
                REPLY_TYPE,
            }
            and self.status == ACTIVE_MESSAGE_STATUS
        )
