# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe.model.document import Document


class AOSConversation(Document):
    def validate(self):
        self._validate_participants()
        self._sort_participants()
        self._set_pair_key()

    def before_save(self):
        self._prevent_participant_modification()

    def _validate_participants(self):
        if not self.participant_1 or not self.participant_2:
            frappe.throw("Both participants are required")

        if self.participant_1 == self.participant_2:
            frappe.throw("You cannot start a conversation with yourself")

        # Public APIs additionally enforce account status and Social policy.
        # The DocType itself still rejects dangling participants so imports,
        # Desk writes and internal callers cannot create invalid references.
        if self.is_new():
            for participant in (self.participant_1, self.participant_2):
                if not frappe.db.exists("User", participant):
                    frappe.throw("Invalid conversation participant")

    def _sort_participants(self):
        users = sorted([self.participant_1, self.participant_2])

        self.participant_1 = users[0]
        self.participant_2 = users[1]

    def _set_pair_key(self):
        material = "\x1f".join([str(self.participant_1 or ""), str(self.participant_2 or "")])
        self.pair_key = hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _prevent_participant_modification(self):
        if self.is_new():
            return

        original = frappe.db.get_value(
            self.doctype,
            self.name,
            ["participant_1", "participant_2"],
            as_dict=True,
        )

        if not original:
            return

        if (
            self.participant_1 != original.participant_1
            or self.participant_2 != original.participant_2
        ):
            frappe.throw("Participants cannot be modified once the conversation is created")

        expected = hashlib.sha256(
            "\x1f".join([str(original.participant_1 or ""), str(original.participant_2 or "")]).encode("utf-8")
        ).hexdigest()
        if self.pair_key != expected:
            frappe.throw("Participant pair key cannot be modified directly")
