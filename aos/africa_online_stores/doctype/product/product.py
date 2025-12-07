# Copyright (c) 2025, Kalutu Daniel and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class Product(Document):
    def after_insert(self):
        try:
            self.create_related_records()
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Product.after_insert failed for {self.name}")
            frappe.throw("An error occurred while creating related records.")

    def on_update(self):
        try:
            self.update_related_records()
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Product.on_update failed for {self.name}")
            frappe.throw("An error occurred while updating related records.")

    def on_trash(self):
        try:
            if self.item_code:
                # Delete Website Item
                website_item_name = frappe.db.exists("Website Item", {"item_code": self.item_code})
                if website_item_name:
                    frappe.delete_doc("Website Item", website_item_name, ignore_permissions=True)

                # Delete Item Price
                item_price_name = frappe.db.exists("Item Price", {
                    "item_code": self.item_code,
                    "price_list": self.get_price_list()
                })
                if item_price_name:
                    frappe.delete_doc("Item Price", item_price_name, ignore_permissions=True)

                # Delete Item
                if frappe.db.exists("Item", self.item_code):
                    frappe.delete_doc("Item", self.item_code, ignore_permissions=True)

        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to delete related records for Product {self.name}")
            frappe.throw("An error occurred while deleting related records.")

    def create_related_records(self):
        item = self.create_item()
        # Set item code
        self.db_set("item_code", item.name)

        self.create_item_price(item.name)
        self.create_website_item(item.name)

    def update_related_records(self):
        item = self.update_item()
        if item:
            self.update_item_price(item.name)
            self.update_website_item(item.name)

    def get_current_vendor(self):
        return frappe.db.get_value("Supplier", {"custom_vendor": frappe.session.user}, "name")

    # ========== ITEM ==========
    def create_item(self):
        try:
            vendor = self.get_current_vendor()

            item = frappe.get_doc({
                "doctype": "Item",
                "item_name": self.item_name,
                "item_group": self.category,
                "stock_uom": "Nos",
                "image": self.image,
                "is_stock_item": self.is_stock_item,
                "include_item_in_manufacturing": 0,
                "custom_vendor": vendor,
                "item_defaults": [{
					"default_supplier": vendor
				}]
            })
            item.insert(ignore_permissions=True)
            return item
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to create Item for Product {self.name}")
            raise

    def update_item(self):
        try:
            if not self.item_code or not frappe.db.exists("Item", self.item_code):
                return None

            item = frappe.get_doc("Item", self.item_code)
            has_changes = False

            if item.item_name != self.item_name:
                item.item_name = self.item_name
                has_changes = True

            if item.item_group != self.category:
                item.item_group = self.category
                has_changes = True

            if item.image != self.image:
                item.image = self.image
                has_changes = True

            if has_changes:
                item.save(ignore_permissions=True)

            return item
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to update Item for Product {self.name}")
            raise

    # ========== ITEM PRICE ==========
    def create_item_price(self, item_code):
        try:
            vendor = self.get_current_vendor()

            frappe.get_doc({
                "doctype": "Item Price",
                "item_code": item_code,
                "price_list": self.get_price_list(),
                "price_list_rate": self.item_price,
                "custom_vendor": vendor
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to create Item Price for {item_code}")
            raise

    def update_item_price(self, item_code):
        try:
            price_list = self.get_price_list()
            item_price_name = frappe.db.exists("Item Price", {
                "item_code": item_code,
                "price_list": price_list
            })

            if item_price_name:
                item_price = frappe.get_doc("Item Price", item_price_name)
                if item_price.price_list_rate != self.item_price:
                    item_price.price_list_rate = self.item_price
                    item_price.save(ignore_permissions=True)
            else:
                self.create_item_price(item_code)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to update Item Price for {item_code}")
            raise

    def get_price_list(self):
        return frappe.db.get_single_value("Webshop Settings", "price_list") or "Standard Selling"

    # ========== WEBSITE ITEM ==========
    def create_or_update_slideshow(self):
        slideshow_items = [row.image for row in self.additional_images]

        if not slideshow_items:
            return None

        slideshow_name = f"{self.name}-Slideshow"
        existing = frappe.db.exists("Website Slideshow", slideshow_name)

        if not existing:
            slideshow = frappe.get_doc({
                "doctype": "Website Slideshow",
                "slideshow_name": slideshow_name,
                "slideshow_items": [
                    {"image": img, "heading": "", "description": "", "url": ""}
                    for img in slideshow_items
                ]
            })
            slideshow.insert(ignore_permissions=True)
        else:
            slideshow = frappe.get_doc("Website Slideshow", slideshow_name)
            slideshow.set("slideshow_items", [
                {"image": img, "heading": "", "description": "", "url": ""}
                for img in slideshow_items
            ])
            slideshow.save(ignore_permissions=True)

        return slideshow_name

    def create_website_item(self, item_code):
        try:
            vendor = self.get_current_vendor()

            website_item = frappe.new_doc("Website Item")
            website_item.item_code = item_code
            website_item.web_item_name = self.item_name
            website_item.published = 1
            website_item.website_image = self.image
            website_item.slideshow = self.create_or_update_slideshow()
            website_item.custom_demo_video = self.demo_video
            website_item.website_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
            website_item.short_description = self.short_description
            website_item.web_long_description = self.web_long_description
            website_item.custom_vendor = vendor

            website_item.set("website_specifications", [])
            for spec in self.website_specifications:
                website_item.append("website_specifications", {
                    "label": spec.label,
                    "description": spec.description
                })

            website_item.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to create Website Item for Product {self.name}")
            raise

    def update_website_item(self, item_code):
        try:
            website_item_name = frappe.db.exists("Website Item", {"item_code": item_code})
            if not website_item_name:
                self.create_website_item(item_code)
                return

            website_item = frappe.get_doc("Website Item", website_item_name)
            has_changes = False

            fields = {
                "web_item_name": self.item_name,
                "published": 1,
                "website_image": self.image,
                "slideshow": self.create_or_update_slideshow(),
                "custom_demo_video": self.demo_video,
                "website_warehouse": frappe.db.get_single_value("Stock Settings", "default_warehouse"),
                "short_description": self.short_description,
                "web_long_description": self.web_long_description,
            }

            for field, new_value in fields.items():
                if getattr(website_item, field) != new_value:
                    setattr(website_item, field, new_value)
                    has_changes = True

            # Compare website specifications
            existing_specs = {(spec.label, spec.description) for spec in website_item.website_specifications}
            new_specs = {(spec.label, spec.description) for spec in self.website_specifications}

            if existing_specs != new_specs:
                website_item.set("website_specifications", [])
                for spec in self.website_specifications:
                    website_item.append("website_specifications", {
                        "label": spec.label,
                        "description": spec.description
                    })
                has_changes = True

            if has_changes:
                website_item.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Failed to update Website Item for Product {self.name}")
            raise
