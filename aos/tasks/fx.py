"""Foreign exchange rate sync job.

This job is designed to be scheduled (e.g., hourly) and will refresh rates only
when needed based on AOS Settings.fx_refresh_hours.

We intentionally keep provider implementations small. You can add providers over
time without changing the calling contract.
"""

from __future__ import annotations

from datetime import timedelta

import frappe

from aos.utils.aos_settings import get_aos_settings_snapshot


def _now():
    return frappe.utils.now_datetime()


def _should_refresh(base_currency: str, refresh_hours: int) -> bool:
    # Find the most recent fetched_at for this base currency
    last = frappe.db.get_value(
        "AOS Exchange Rate",
        {"base_currency": base_currency, "is_active": 1},
        "fetched_at",
        order_by="fetched_at desc",
    )
    if not last:
        return True

    try:
        last_dt = frappe.utils.get_datetime(last)
    except Exception:
        return True

    return (_now() - last_dt) >= timedelta(hours=int(refresh_hours or 12))


def _fetch_rates_stub(base_currency: str) -> dict[str, float]:
    """Placeholder provider implementation.

    You can plug in a real provider later (OpenExchangeRates, Fixer, etc.).
    This stub intentionally returns an empty dict when no provider is configured.
    """

    return {}


def update_fx_rates(force: bool = False) -> None:
    """Refresh FX rates into 'AOS Exchange Rate'.

    Safe to run frequently; will no-op when not due.
    """

    settings = get_aos_settings_snapshot(use_cache=False)
    base = (settings.base_currency or "USD")
    refresh_hours = int(settings.fx_refresh_hours or 12)

    if not force and not _should_refresh(base, refresh_hours):
        return

    provider = (settings.fx_provider or "").strip()

    # For now we only run when a provider is set and an API key exists.
    api_key = None
    try:
        api_key = frappe.get_single("AOS Settings").get_password("fx_api_key")
    except Exception:
        api_key = None

    if not provider or not api_key:
        # Don't error; just log so it can be configured in production.
        frappe.logger("aos").info("FX sync skipped: provider/api key not configured.")
        return

    # TODO: Implement provider fetchers.
    rates = _fetch_rates_stub(base)
    if not rates:
        frappe.logger("aos").warning("FX sync produced no rates. Please check provider configuration.")
        return

    fetched_at = _now()

    # Upsert quotes
    for quote, rate in rates.items():
        quote = (quote or "")
        if not quote or quote == base:
            continue
        try:
            docname = frappe.db.get_value(
                "AOS Exchange Rate", {"base_currency": base, "quote_currency": quote}, "name"
            )
            if docname:
                frappe.db.set_value(
                    "AOS Exchange Rate",
                    docname,
                    {
                        "rate": float(rate),
                        "fetched_at": fetched_at,
                        "source": provider,
                        "is_active": 1,
                    },
                )
            else:
                doc = frappe.get_doc(
                    {
                        "doctype": "AOS Exchange Rate",
                        "base_currency": base,
                        "quote_currency": quote,
                        "rate": float(rate),
                        "fetched_at": fetched_at,
                        "source": provider,
                        "is_active": 1,
                    }
                )
                doc.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS FX Sync Upsert Failed")

    frappe.db.commit()
