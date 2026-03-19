# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSConversation(Document):
    def validate(self):
        self._validate_participants()
        self._sort_participants()

    def before_save(self):
        self._prevent_participant_modification()

    def _validate_participants(self):
        if not self.participant_1 or not self.participant_2:
            frappe.throw("Both participants are required")

        if self.participant_1 == self.participant_2:
            frappe.throw("You cannot start a conversation with yourself")

    def _sort_participants(self):
        users = sorted([self.participant_1, self.participant_2])

        self.participant_1 = users[0]
        self.participant_2 = users[1]

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
