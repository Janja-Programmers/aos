"""Fresh-site installation defaults owned by AOS."""

from __future__ import annotations

import frappe


def after_install() -> None:
    """Disable Frappe's parallel public signup UI/API on fresh AOS sites."""
    frappe.db.set_single_value("Website Settings", "disable_signup", 1)


def before_tests() -> None:
    """Reassert schema-only invariants after Frappe prepares the test site.

    ``bench run-tests`` may synchronize DocTypes after the user's preceding
    ``bench migrate``. Manual composite/unique indexes are not represented in
    DocType JSON and therefore must be reasserted once the test schema is ready.
    This installs schema only; it does not create or commit business fixtures.
    """
    from aos.migrate import after_migrate

    after_migrate()
