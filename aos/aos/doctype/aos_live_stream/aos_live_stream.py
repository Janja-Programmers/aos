# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime

from aos.api.shared.market_context import resolve_market_country


ACTIVE_STATUSES = {"live"}
TERMINAL_STATUSES = {"ended"}


class AOSLiveStream(Document):
    def validate(self):
        self._validate_seller()
        self._validate_status_transition()
        self._validate_single_active_live_per_seller()

    def before_insert(self):
        self._set_initial_state()
        self._set_country()

    def before_save(self):
        self._handle_status_side_effects()
        self._compute_duration()

    def after_insert(self):
        self._set_room_name()

    # VALIDATIONS
    def _validate_seller(self):
        if not self.seller:
            frappe.throw("Seller is required")

        seller = frappe.db.get_value(
            "AOS Seller",
            self.seller,
            ["name", "status"],
            as_dict=True,
        )

        if not seller:
            frappe.throw("Invalid seller.")

        if seller.status != "Active":
            frappe.throw("Seller account is not active.")

    def _validate_status_transition(self):
        if self.is_new():
            return

        old_status = self.get_db_value("status")

        if old_status == self.status:
            return

        valid_transitions = {
            "scheduled": {"live", "ended"},
            "live": {"ended"},
            "ended": set(),
        }

        allowed = valid_transitions.get(old_status, set())

        if self.status not in allowed:
            frappe.throw(f"Invalid status transition: {old_status} → {self.status}")

    def _validate_single_active_live_per_seller(self):
        if self.status not in ACTIVE_STATUSES:
            return

        existing = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Live Stream`
            WHERE seller=%s AND is_active=1 AND name!=%s
            LIMIT 1
            """,
            (self.seller, self.name or ""),
        )

        if existing:
            frappe.throw("Seller already has an active live stream")

    # SETTERS
    def _set_initial_state(self):
        if not self.status:
            self.status = "scheduled"

        if self.status == "live":
            self.is_active = 1
        else:
            self.is_active = 0

    def _set_room_name(self):
        if not self.room_name:
            self.db_set("room_name", f"live:{self.name}")

    def _set_country(self):
        """
        Set country from market context if not provided.
        """
        if self.country:
            return

        market_country, err = resolve_market_country(None)

        if err:
            frappe.throw(err.get("message") or "Failed to resolve market country")

        self.country = market_country

    # STATUS SIDE EFFECTS
    def _handle_status_side_effects(self):
        now = now_datetime()

        # Started
        if self.status == "live":
            if not self.started_at:
                self.started_at = now

            self.is_active = 1

        # Ended
        if self.status in TERMINAL_STATUSES:
            if not self.started_at:
                frappe.throw("Cannot end a live that never started.")

            if not self.ended_at:
                self.ended_at = now

            self.is_active = 0

    # COMPUTATIONS
    def _compute_duration(self):
        if not self.started_at or not self.ended_at:
            return

        started_at = get_datetime(self.started_at)
        ended_at = get_datetime(self.ended_at)

        if ended_at < started_at:
            frappe.throw("Invalid timestamps: ended_at is before started_at")

        delta = ended_at - started_at
        self.duration_seconds = int(delta.total_seconds())
