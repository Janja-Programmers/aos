"""
Foreign Exchange background jobs.

Jobs:
- update_exchange_rates: Fetch latest FX rates from exchangerate.host
  and update AOS Exchange Rate table.

Design:
- Uses base_currency from AOS Settings
- Uses fx_api_key (Password field)
- Filters only currencies present in Currency DocType
- Upserts by currency (autoname = field:currency)
- Updates last_updated timestamp
- Safe logging
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import frappe
from frappe.utils import now_datetime

from aos.utils.aos_settings import get_aos_settings_snapshot


API_URL = "https://api.exchangerate.host/live"


def update_exchange_rates() -> None:
    """Fetch and update exchange rates relative to base currency."""

    try:
        # Load Settings Snapshot
        snap = get_aos_settings_snapshot(use_cache=False)

        base_currency = snap.base_currency
        refresh_hours = snap.refresh_hours

        if not base_currency:
            frappe.logger().warning(
                "AOS FX: base_currency not configured. Skipping update."
            )
            return

        settings = frappe.get_single("AOS Settings")
        api_key = settings.get_password("fx_api_key")

        if not api_key:
            frappe.logger().warning(
                "AOS FX: fx_api_key not configured. Skipping update."
            )
            return

        # Refresh Interval Check
        last_record = frappe.db.get_value(
            "AOS Exchange Rate",
            filters={},
            fieldname="last_updated",
            order_by="last_updated desc",
        )

        if last_record:
            now = now_datetime()
            delta = now - last_record

            if delta < timedelta(hours=refresh_hours):
                # Not time yet
                return

        # Build API Request
        url = f"{API_URL}?access_key={api_key}&source={base_currency}"

        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                raw = response.read()
        except urllib.error.URLError as e:
            frappe.log_error(
                title="AOS FX API Connection Failed",
                message=str(e),
            )
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            frappe.log_error(
                title="AOS FX Invalid JSON",
                message=str(raw),
            )
            return

        # Validate API Response
        if not payload.get("success"):
            frappe.log_error(
                title="AOS FX API Returned Error",
                message=str(payload),
            )
            return

        source = payload.get("source")
        quotes = payload.get("quotes") or {}

        if source != base_currency:
            frappe.log_error(
                title="AOS FX Base Currency Mismatch",
                message=f"Expected {base_currency}, got {source}",
            )
            return

        if not quotes:
            frappe.logger().warning("AOS FX: No quotes returned.")
            return

        # Load Supported Currencies
        supported = set(
            frappe.get_all("Currency", pluck="name")
        )

        now_ts = now_datetime()
        updated_count = 0

        # Process Quotes
        for key, rate in quotes.items():
            # key format: USDKES
            if not key.startswith(base_currency):
                continue

            currency = key[len(base_currency):]

            if currency not in supported:
                continue

            try:
                rate = float(rate)
            except Exception:
                continue

            if rate <= 0:
                continue

            # Upsert record
            if frappe.db.exists("AOS Exchange Rate", currency):
                doc = frappe.get_doc("AOS Exchange Rate", currency)
                doc.rate_vs_base = rate
                doc.last_updated = now_ts
                doc.save(ignore_permissions=True)
            else:
                doc = frappe.get_doc({
                    "doctype": "AOS Exchange Rate",
                    "currency": currency,
                    "rate_vs_base": rate,
                    "last_updated": now_ts,
                })
                doc.insert(ignore_permissions=True)

            updated_count += 1

        frappe.db.commit()

        if updated_count:
            frappe.logger().info(
                f"AOS FX: Updated {updated_count} exchange rates (base={base_currency})."
            )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS FX update_exchange_rates failed",
        )
