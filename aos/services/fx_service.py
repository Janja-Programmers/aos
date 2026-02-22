"""
FX conversion service.

Provides safe currency conversion using AOS Exchange Rate table.
Never calls external API. Uses cached DB rates.
"""

from __future__ import annotations

from typing import Dict

import frappe

from aos.utils.aos_settings import get_aos_settings_snapshot


CACHE_KEY = "aos:fx:rates:v1"
CACHE_TTL = 60 * 5  # 5 minutes


def _load_rates() -> Dict[str, float]:
    """Load all exchange rates into memory with caching."""

    cache = frappe.cache()

    cached = cache.get_value(CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    rows = frappe.get_all(
        "AOS Exchange Rate",
        fields=["currency", "rate_vs_base"],
    )

    rates = {}

    for r in rows:
        try:
            rates[r["currency"]] = float(r["rate_vs_base"])
        except Exception:
            continue

    cache.set_value(CACHE_KEY, rates, expires_in_sec=CACHE_TTL)

    return rates


def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> float:
    """
    Convert amount between currencies using cross-rate formula.

    amount: numeric
    from_currency: ISO code
    to_currency: ISO code
    """

    try:
        amount = float(amount)
    except Exception:
        return float(amount or 0)

    if not from_currency or not to_currency:
        return amount

    if from_currency == to_currency:
        return amount

    snap = get_aos_settings_snapshot()
    base = snap.base_currency

    if not base:
        return amount  # fallback safely

    rates = _load_rates()

    # Treat base currency as 1
    rate_source = 1.0 if from_currency == base else rates.get(from_currency)
    rate_target = 1.0 if to_currency == base else rates.get(to_currency)

    if not rate_source or not rate_target:
        # Missing rate → do not crash, fallback
        return amount

    try:
        converted = amount * (rate_target / rate_source)
        return float(round(converted, 6))
    except Exception:
        return amount