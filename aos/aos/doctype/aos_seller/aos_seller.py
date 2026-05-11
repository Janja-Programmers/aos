# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AOSSeller(Document):
    def validate(self):
        self._validate_user()
        self._validate_operating_hours()

    def _validate_user(self):
        if not self.user:
            frappe.throw(_("User is required."))

    def _validate_operating_hours(self):
        if not self.operating_hours:
            return

        seen_days = set()

        for row in self.operating_hours:
            day = row.day_of_week

            if day in seen_days:
                frappe.throw(
                    _("Duplicate entry for {0} in operating hours.").format(day)
                )

            seen_days.add(day)

            if row.is_open:
                if not row.open_time or not row.close_time:
                    frappe.throw(
                        _(
                            "{0}: Open and close time are required when business is open."
                        ).format(day)
                    )

                if row.open_time >= row.close_time:
                    frappe.throw(
                        _("{0}: Open time must be earlier than close time.").format(
                            day
                        )
                    )
            else:
                row.open_time = None
                row.close_time = None
