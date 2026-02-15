from __future__ import annotations
import frappe
from aos.api.shared.auth import require_login
from aos.api.shared.responses import ok, fail

_DT = "AOS Saved Search"


def list_saved_searches_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rows = frappe.get_all(
        _DT,
        filters={"user": user, "is_active": 1},
        fields=[
            "name",
            "title",
            "params_json",
            "use_count",
            "last_used",
        ],
        order_by="last_used desc",
    )

    return ok("Saved searches fetched.", data={"items": rows})
