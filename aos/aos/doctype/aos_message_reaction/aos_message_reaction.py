# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


MAX_EMOJI_LENGTH = 16


class AOSMessageReaction(Document):
    def validate(self):
        self._validate_required_fields()
        self._validate_emoji()
        self._validate_message_and_conversation()
        self._validate_user_is_participant()
        self._validate_message_visibility()
        self._prevent_duplicate_reaction()

    def _validate_required_fields(self):
        if not self.message:
            frappe.throw("Message is required")

        if not self.conversation:
            frappe.throw("Conversation is required")

        if not self.user:
            frappe.throw("User is required")

        if not self.emoji:
            frappe.throw("Emoji is required")

    def _validate_emoji(self):
        emoji = (self.emoji or "").strip()

        if not emoji:
            frappe.throw("Emoji is required")

        if len(emoji) > MAX_EMOJI_LENGTH:
            frappe.throw(f"Emoji cannot exceed {MAX_EMOJI_LENGTH} characters")

        self.emoji = emoji

    def _validate_message_and_conversation(self):
        message = frappe.db.get_value(
            "AOS Message",
            self.message,
            [
                "name",
                "conversation",
                "deleted_for_everyone",
            ],
            as_dict=True,
        )

        if not message:
            frappe.throw("Invalid message")

        if not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")

        if message.conversation != self.conversation:
            frappe.throw("Message does not belong to this conversation")

        self._message_row = message

    def _validate_user_is_participant(self):
        conversation = frappe.db.get_value(
            "AOS Conversation",
            self.conversation,
            [
                "participant_1",
                "participant_2",
            ],
            as_dict=True,
        )

        if not conversation:
            frappe.throw("Invalid conversation")

        if self.user not in (
            conversation.participant_1,
            conversation.participant_2,
        ):
            frappe.throw("User must be a participant in the conversation")

        self._conversation_row = conversation

    def _validate_message_visibility(self):
        """
        Prevent reacting to messages that are not visible to this user.

        Rules:
        - deleted_for_everyone messages cannot be reacted to.
        - deleted_for_1/deleted_for_2 messages cannot be reacted to by that viewer.
        """

        if bool(self._message_row.deleted_for_everyone):
            frappe.throw("Deleted messages cannot be reacted to")

        delete_field = (
            "deleted_for_1"
            if self.user == self._conversation_row.participant_1
            else "deleted_for_2"
        )

        is_deleted_for_user = frappe.db.get_value(
            "AOS Message",
            self.message,
            delete_field,
        )

        if bool(is_deleted_for_user):
            frappe.throw("You cannot react to a message deleted for you")

    def _prevent_duplicate_reaction(self):
        """
        Friendly validation before the DB unique constraint catches duplicates.

        The API should update an existing reaction instead of inserting another one.
        """

        filters = {
            "message": self.message,
            "user": self.user,
        }

        if not self.is_new():
            filters["name"] = ["!=", self.name]

        existing = frappe.db.exists("AOS Message Reaction", filters)

        if existing:
            frappe.throw("Message already has a reaction by this user")
