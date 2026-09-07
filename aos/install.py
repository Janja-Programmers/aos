"""Fresh-site installation defaults owned by AOS."""

from __future__ import annotations

import frappe


def after_install() -> None:
    """Disable Frappe's parallel public signup UI/API on fresh AOS sites."""
    frappe.db.set_single_value("Website Settings", "disable_signup", 1)
