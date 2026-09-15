"""Versioned foreign-exchange refresh job.

The provider is used only here. A refresh is all-or-nothing from the reader's
perspective because all rows share one rate_version/base/timestamp. Old data is
left in place when fetch/validation fails and read paths enforce a maximum age.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
import uuid

import frappe
from frappe.utils import now_datetime

from aos.services.fx_service import clear_fx_cache
from aos.utils.aos_config import get_fx_api_key
from aos.utils.aos_settings import get_aos_settings_snapshot

API_URL = "https://api.exchangerate.host/live"


def _provider_time(payload: dict):
    value = payload.get("timestamp")
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return now_datetime()


def update_exchange_rates() -> None:
    snap = get_aos_settings_snapshot(use_cache=False)
    base = str(snap.base_currency or "").strip()
    api_key = get_fx_api_key()
    if not base or not api_key:
        frappe.logger().warning("AOS FX refresh skipped: configuration incomplete.")
        return

    lock_name = f"aos:fx:refresh:{getattr(frappe.local, 'site', 'default')}"
    try:
        lock = frappe.cache().lock(lock_name, timeout=30, blocking_timeout=1)
    except TypeError:
        lock = frappe.cache().lock(lock_name, timeout=30)

    with lock:
        last = frappe.db.get_value("AOS Exchange Rate", filters={}, fieldname="last_updated", order_by="last_updated desc")
        if last and now_datetime() - last < timedelta(hours=snap.refresh_hours):
            return

        request = urllib.request.Request(f"{API_URL}?access_key={api_key}&source={base}", headers={"Accept":"application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(2_000_000)
        except urllib.error.URLError:
            frappe.log_error("FX provider unavailable.", "AOS FX Provider Unavailable")
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            frappe.log_error("FX provider returned invalid JSON.", "AOS FX Invalid Response")
            return
        if payload.get("success") is not True or str(payload.get("source") or "") != base:
            frappe.log_error("FX provider response failed validation.", "AOS FX Invalid Response")
            return

        supported = set(frappe.get_all("Currency", pluck="name", limit=500))
        rates: dict[str, float] = {base: 1.0}
        for key, value in (payload.get("quotes") or {}).items():
            if not str(key).startswith(base):
                continue
            currency = str(key)[len(base):]
            if currency not in supported:
                continue
            try:
                rate = float(value)
            except Exception:
                continue
            if rate > 0:
                rates[currency] = rate
        if len(rates) < 2:
            frappe.log_error("FX provider returned no usable rates.", "AOS FX Empty Snapshot")
            return

        captured_at = now_datetime()
        provider_at = _provider_time(payload)
        if captured_at - provider_at > timedelta(hours=snap.fx_max_stale_hours):
            frappe.log_error("FX provider snapshot is stale.", "AOS FX Stale Snapshot")
            return
        version = f"{base}:{int(provider_at.timestamp())}:{uuid.uuid4().hex[:12]}"

        # One database transaction replaces the complete supported snapshot.
        # Readers either observe the previous complete version or the new one;
        # obsolete currencies are removed in the same transaction so mixed
        # rate_version sets cannot survive a successful refresh.
        existing = set(frappe.get_all("AOS Exchange Rate", pluck="name", limit=1000))
        for currency, rate in rates.items():
            doc = frappe.get_doc("AOS Exchange Rate", currency) if currency in existing else frappe.new_doc("AOS Exchange Rate")
            doc.currency = currency
            doc.rate_vs_base = rate
            doc.base_currency = base
            doc.rate_version = version
            doc.provider_timestamp = provider_at
            doc.last_updated = captured_at
            if doc.is_new():
                doc.insert(ignore_permissions=True)
            else:
                doc.save(ignore_permissions=True)

        obsolete = sorted(existing - set(rates))
        if obsolete:
            frappe.db.delete("AOS Exchange Rate", {"name": ["in", obsolete]})

        manager = getattr(frappe.db, "after_commit", None)
        if manager is not None and hasattr(manager, "add"):
            manager.add(clear_fx_cache)
        frappe.logger().info("AOS FX snapshot refreshed: %s rates, base=%s, version=%s", len(rates), base, version)
