# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSSeller(Document):
    def validate(self):
        self._validate_operating_hours()

    def _validate_operating_hours(self):
        if not self.operating_hours:
            return

        seen_days = set()

        for row in self.operating_hours:
            # DUPLICATE DAY CHECK
            if row.day_of_week in seen_days:
                frappe.throw(
                    f"Duplicate entry for {row.day_of_week} in operating hours."
                )
            seen_days.add(row.day_of_week)

            # OPEN / CLOSE LOGIC
            if row.is_open:
                if not row.open_time or not row.close_time:
                    frappe.throw(
                        f"{row.day_of_week}: Open and Close time are required when shop is open."
                    )


                # TIME VALIDATION
                if row.open_time and row.close_time:
                    if row.open_time >= row.close_time:
                        frappe.throw(
                            f"{row.day_of_week}: Open time must be earlier than close time."
                        )

            else:
                # If closed → ignore times (normalize data)
                row.open_time = None
                row.close_time = None
