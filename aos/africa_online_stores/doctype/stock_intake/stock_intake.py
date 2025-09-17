# Copyright (c) 2025, Kalutu Daniel and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class StockIntake(Document):
    def validate(self):
        self._validate_quantities()
        self._validate_duplicates()

    def _validate_quantities(self):
        for row in self.items:
            if row.qty <= 0:
                frappe.throw(
                    title="Invalid Quantity",
                    msg=f"Quantity for item <b>{row.item}</b> must be greater than 0."
                )

    def _validate_duplicates(self):
        seen_items = set()
        for row in self.items:
            if row.item in seen_items:
                frappe.throw(
                    title="Duplicate Item",
                    msg=f"Item <b>{row.item}</b> has been added more than once."
                )
            seen_items.add(row.item)

    def on_submit(self):
        company = self._get_default_company()
        warehouse = self._get_default_warehouse()

        try:
            stock_entry = frappe.new_doc("Stock Entry")
            stock_entry.stock_entry_type = "Material Receipt"
            stock_entry.company = company
            stock_entry.custom_vendor = self.vendor

            for row in self.items:
                item_data = {
                    "item_code": row.item,
                    "qty": row.qty,
                    "t_warehouse": warehouse
                }

                if row.get("valuation_rate"):
                    item_data["basic_rate"] = row.valuation_rate
                else:
                    item_data["allow_zero_valuation_rate"] = 1

                stock_entry.append("items", item_data)

            stock_entry.insert()
            stock_entry.submit()
            self.db_set("stock_entry", stock_entry.name)

        except Exception:
            frappe.logger("Stock Intake").error(
                f"Error creating Stock Entry for Stock Intake '{self.name}':\n{frappe.get_traceback()}"
            )
            frappe.throw("An error occurred while creating the Stock Entry. Please contact your administrator.")

    def on_cancel(self):
        if not self.stock_entry:
            return

        try:
            stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
            if stock_entry.docstatus == 1:
                stock_entry.cancel()
        except frappe.DoesNotExistError:
            frappe.logger("Stock Intake").warning(
                f"Stock Entry '{self.stock_entry}' not found while cancelling Stock Intake '{self.name}'."
            )

    def _get_default_company(self):
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not company:
            frappe.throw("Default Company is not set in Global Defaults.")
        return company

    def _get_default_warehouse(self):
        warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        if not warehouse:
            frappe.throw("Default Warehouse is not set in Stock Settings.")
        return warehouse
