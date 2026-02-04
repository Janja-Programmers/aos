"""FX utilities.

We store exchange rates in AOS Exchange Rate as BASE->QUOTE. The base currency
is configured in AOS Settings (defaults to USD).

This module provides:
  - get_rate(base, quote)
  - convert(amount, from_currency, to_currency)

Note: This does not fetch rates from external providers; that is handled by a
scheduler job (see aos/tasks/fx.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from .aos_settings import get_aos_settings_snapshot


@dataclass(frozen=True)
class FXQuote:
    rate: float
    fetched_at: str | None
    source: str | None


def get_rate(base_currency: str, quote_currency: str) -> FXQuote | None:
    base_currency = (base_currency or "")
    quote_currency = (quote_currency or "")

    if not base_currency or not quote_currency:
        return None

    row = frappe.db.get_value(
        "AOS Exchange Rate",
        {"base_currency": base_currency, "quote_currency": quote_currency, "is_active": 1},
        ["rate", "fetched_at", "source"],
        as_dict=True,
    )
    if not row or row.get("rate") is None:
        return None

    return FXQuote(rate=float(row["rate"]), fetched_at=str(row.get("fetched_at") or "") or None, source=row.get("source") or None)


def convert(amount: float, from_currency: str, to_currency: str) -> tuple[float | None, dict]:
    """Convert amount between currencies using stored base rates.

    Returns (converted_amount, meta). If conversion isn't possible, returns (None, meta).
    """

    settings = get_aos_settings_snapshot()
    base = (settings.base_currency or "USD")

    f = (from_currency or "")
    t = (to_currency or "")

    meta = {
        "base_currency": base,
        "from": f,
        "to": t,
        "rate_used": None,
        "fetched_at": None,
        "source": None,
    }

    if not f or not t:
        return None, meta

    if f == t:
        return float(amount), meta

    # If converting from base -> quote
    if f == base:
        q = get_rate(base, t)
        if not q:
            return None, meta
        meta.update({"rate_used": q.rate, "fetched_at": q.fetched_at, "source": q.source})
        return float(amount) * q.rate, meta

    # If converting quote -> base
    if t == base:
        q = get_rate(base, f)
        if not q or not q.rate:
            return None, meta
        rate = 1.0 / q.rate
        meta.update({"rate_used": rate, "fetched_at": q.fetched_at, "source": q.source})
        return float(amount) * rate, meta

    # Cross conversion: from -> base -> to
    q_from = get_rate(base, f)
    q_to = get_rate(base, t)
    if not q_from or not q_to or not q_from.rate:
        return None, meta

    cross_rate = q_to.rate / q_from.rate
    meta.update({"rate_used": cross_rate, "fetched_at": q_to.fetched_at or q_from.fetched_at, "source": q_to.source or q_from.source})
    return float(amount) * cross_rate, meta
