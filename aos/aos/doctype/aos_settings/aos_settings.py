# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from aos.services.localization_service import validate_country, validate_currency, validate_language
from aos.utils.aos_settings import clear_aos_settings_cache


class AOSSettings(Document):
    def validate(self):
        checks = (
            ("default_country", self.default_country, validate_country),
            ("default_currency", self.default_currency, validate_currency),
            ("default_language", self.default_language, validate_language),
        )
        for fieldname, value, validator in checks:
            resolved, error = validator(value)
            if error:
                frappe.throw(f"{fieldname.replace('_', ' ').title()} must reference an enabled, valid master record.", frappe.ValidationError)
            setattr(self, fieldname, resolved)

        if self.base_currency:
            resolved, error = validate_currency(self.base_currency)
            if error:
                frappe.throw("Base Currency must reference an enabled Currency.", frappe.ValidationError)
            self.base_currency = resolved

    def on_update(self):
        clear_aos_settings_cache()
