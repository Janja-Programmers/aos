# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.live_analytics_service import LiveAnalyticsService


LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"
USER_DOCTYPE = "User"

ACTIVE_MESSAGE_STATUS = "active"
LIVE_STATUS = "live"
ENDED_STATUS = "ended"

COMMENT_KIND = "comment"
SYSTEM_KIND = "system"
COHOST_KIND = "cohost"
GIFT_KIND = "gift"
MODERATION_KIND = "moderation"

COMMENT_TYPE = "comment"
REPLY_TYPE = "reply"

VALID_MESSAGE_STATUSES = {
    "active",
    "hidden",
    "deleted",
}

VALID_MESSAGE_KINDS = {
    COMMENT_KIND,
    SYSTEM_KIND,
    COHOST_KIND,
    GIFT_KIND,
    MODERATION_KIND,
}

MESSAGE_TYPES_BY_KIND = {
    COMMENT_KIND: {
        COMMENT_TYPE,
        REPLY_TYPE,
    },
    SYSTEM_KIND: {
        "live_started",
        "notifying_followers",
        "viewer_joined",
        "live_ended",
        "generic",
    },
    COHOST_KIND: {
        "cohost_invited",
        "cohost_request_sent",
        "cohost_request_accepted",
        "cohost_request_rejected",
        "cohost_started",
        "cohost_ended",
        "generic",
    },
    GIFT_KIND: {
        "generic",
    },
    MODERATION_KIND: {
        "generic",
    },
}

MESSAGE_TYPES_ALLOWED_WHEN_LIVE_INACTIVE = {
    "live_ended",
    "cohost_ended",
    "generic",
}

HOST_ONLY_MESSAGE_TYPES = {
    "notifying_followers",
    "viewer_joined",
    "cohost_request_sent",
}

TARGET_ONLY_MESSAGE_TYPES = {
    "cohost_invited",
}

IMMUTABLE_FIELDS = {
    "live_stream",
    "message_kind",
    "message_type",
    "user",
    "target_user",
    "parent_message",
    "root_message",
    "idempotency_key",
}


class AOSLiveMessage(Document):
    def validate(self):
        self._normalize_values()
        self._sync_active_idempotency_key()
        self._validate_required_fields()
        self._validate_message_kind_and_type()
        self._validate_status()
        self._validate_immutable_fields()
        self._validate_user()
        self._validate_target_user()
        self._validate_live_stream()
        self._validate_parent_message()
        self._validate_metadata()
        self._normalize_visibility()

    def _sync_active_idempotency_key(self):
        key = str(getattr(self, "idempotency_key", "") or "").strip()
        self.idempotency_key = key or None
        if key and self.message_kind == COMMENT_KIND and self.status == ACTIVE_MESSAGE_STATUS:
            self.active_idempotency_key = f"{self.live_stream}|{self.user}|{key}"
        else:
            self.active_idempotency_key = None

    def before_insert(self):
        self._set_root_message()

    def after_insert(self):
        self._increment_reply_count()
        self._increment_live_comment_count()

    def on_update(self):
        """
        Synchronize active comment and reply counters when status changes
        through a normal document save.

        Direct database updates must synchronize counters explicitly.
        """
        previous = self.get_doc_before_save()

        if not previous:
            return

        if previous.status == self.status:
            return

        if self.message_kind != COMMENT_KIND:
            return

        LiveAnalyticsService.sync_comment_count(
            live_id=self.live_stream,
        )

        if self._is_reply():
            self._sync_parent_reply_count(
                self.parent_message
            )

    def on_trash(self):
        """
        Permit permanent deletion only after the live has ended.

        When deleting a parent message, permanently remove all descendant
        replies first so no orphaned thread rows remain.

        Historical Live Stream.comment_count is intentionally preserved.
        """
        live_status = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            self.live_stream,
            "status",
        )

        if not live_status:
            frappe.throw(
                "Invalid live stream."
            )

        if live_status != ENDED_STATUS:
            frappe.throw(
                "Live messages can only be permanently deleted "
                "after the live has ended."
            )

        self._deleted_parent_message = self.parent_message

        descendant_ids = (
            self._get_descendant_message_ids()
        )

        if descendant_ids:
            frappe.db.delete(
                LIVE_MESSAGE_DOCTYPE,
                {
                    "name": [
                        "in",
                        descendant_ids,
                    ],
                },
            )

    def after_delete(self):
        """
        Repair the surviving parent's reply_count after an administrator
        permanently deletes a reply.

        Live Stream.comment_count is not changed because it represents
        historical engagement during the live.
        """

        parent_id = getattr(
            self,
            "_deleted_parent_message",
            self.parent_message,
        )

        if parent_id:
            self._sync_parent_reply_count(
                parent_id
            )

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
        Enforce visibility rules that must not depend on API input.
        """
        if self.message_kind == COMMENT_KIND:
            self.visible_to_host = 1
            self.visible_to_viewers = 1
            return

        if self.message_type in HOST_ONLY_MESSAGE_TYPES:
            self.visible_to_host = 1
            self.visible_to_viewers = 0
            return

        if self.message_type in TARGET_ONLY_MESSAGE_TYPES:
            self.visible_to_host = 0
            self.visible_to_viewers = 0
            return

        self.visible_to_host = int(
            bool(self.visible_to_host)
        )

        self.visible_to_viewers = int(
            bool(self.visible_to_viewers)
        )

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

    def _validate_immutable_fields(self):
        """
        Prevent moving, reclassifying, reassigning, or rethreading an
        existing live message.
        """
        if self.is_new():
            return

        previous = self.get_doc_before_save()

        if not previous:
            return

        for fieldname in IMMUTABLE_FIELDS:
            old_value = previous.get(
                fieldname
            )

            new_value = self.get(
                fieldname
            )

            if old_value == new_value:
                continue

            label = (
                self.meta.get_label(fieldname)
                or fieldname.replace(
                    "_",
                    " ",
                ).title()
            )

            frappe.throw(
                f"{label} cannot be changed after the live message "
                f"has been created."
            )

    def _validate_user(self):
        """
        Comments and replies require an enabled author.

        Other message kinds may exist without an actor user.
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
            if (
                self.message_type
                in TARGET_ONLY_MESSAGE_TYPES
            ):
                frappe.throw(
                    "Target user is required for this message type."
                )

            return

        target = frappe.db.get_value(
            USER_DOCTYPE,
            self.target_user,
            [
                "name",
                "enabled",
            ],
            as_dict=True,
        )

        if not target:
            frappe.throw(
                "Invalid target user."
            )

        if not bool(target.enabled):
            frappe.throw(
                "Target user account is disabled."
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

        # Existing messages may still be moderated after the live ends.
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

            if self.root_message:
                frappe.throw(
                    "A top-level comment cannot have a root message."
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

        if parent.message_type not in {
            COMMENT_TYPE,
            REPLY_TYPE,
        }:
            frappe.throw(
                "Replies can only target comments or replies."
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
        Set thread identity internally.

        Top-level comments have no root message. Every reply points to the
        original top-level comment in its thread.
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

    def _increment_live_comment_count(self):
        if not self._should_affect_comment_count():
            return

        LiveAnalyticsService.handle_comment_added(
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
    def _get_descendant_message_ids(
        self,
    ) -> list[str]:
        """
        Return all descendant replies below the current message.
        """

        collected: list[str] = []
        pending: list[str] = [
            self.name,
        ]

        while pending:
            parent_ids = list(
                pending
            )

            pending = []

            children = frappe.get_all(
                LIVE_MESSAGE_DOCTYPE,
                filters={
                    "parent_message": [
                        "in",
                        parent_ids,
                    ],
                },
                pluck="name",
            )

            for child_id in children:
                if child_id in collected:
                    continue

                collected.append(
                    child_id
                )

                pending.append(
                    child_id
                )

        return collected

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
        Only active comments and replies affect live analytics while the
        live message records still exist.
        """
        return bool(
            self.message_kind == COMMENT_KIND
            and self.message_type in {
                COMMENT_TYPE,
                REPLY_TYPE,
            }
            and self.status == ACTIVE_MESSAGE_STATUS
        )
