"""Opaque-ID and provisioning indexes for canonical Calls."""
from __future__ import annotations

import frappe
from aos.patches.v1_0.install_call_indexes import _ensure_index


def execute() -> None:
    if not frappe.db.table_exists("AOS Call"):
        return
    _ensure_index("AOS Call", "uq_call_public_id", ("public_id",), unique=True)
    _ensure_index("AOS Call", "idx_call_provision_recovery", ("status", "is_active", "rtc_provisioned_at", "creation", "name"), unique=False)
