"""Database-backed FX snapshots for Marketplace Discovery.

External providers are never called on request/read paths.  A complete refresh
writes one versioned snapshot; readers reject stale/mixed snapshots instead of
silently inventing conversions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict

import frappe
from frappe.utils import get_datetime, now_datetime

from aos.services.currency_conversion import ConversionResult, convert_amount
from aos.utils.aos_settings import get_aos_settings_snapshot

CACHE_TTL = 60 * 5


@dataclass(frozen=True)
class FXSnapshot:
    base_currency: str
    rate_version: str
    as_of: object | None
    rates: Dict[str, float]
    fresh: bool


def _site_key() -> str:
    site = str(getattr(getattr(frappe, "local", None), "site", "") or "default").strip()
    return f"aos:{site}:fx:snapshot:v2"


def clear_fx_cache() -> None:
    try:
        frappe.cache().delete_value(_site_key())
    except Exception:
        pass


def get_fx_snapshot(*, use_cache: bool = True) -> FXSnapshot:
    settings = get_aos_settings_snapshot()
    base = str(settings.base_currency or "").strip()
    cache = frappe.cache()
    key = _site_key()
    if use_cache:
        cached = cache.get_value(key)
        if isinstance(cached, dict) and cached.get("base_currency") == base:
            try:
                as_of = get_datetime(cached.get("as_of")) if cached.get("as_of") else None
                fresh = bool(as_of and now_datetime() - as_of <= timedelta(hours=settings.fx_max_stale_hours))
                return FXSnapshot(base, str(cached.get("rate_version") or ""), as_of, dict(cached.get("rates") or {}), fresh)
            except Exception:
                pass

    rows = frappe.get_all(
        "AOS Exchange Rate",
        fields=["currency", "rate_vs_base", "base_currency", "rate_version", "provider_timestamp", "last_updated"],
        order_by="currency asc",
        limit=500,
    )
    rates: Dict[str, float] = {}
    versions = set()
    bases = set()
    timestamps = []
    for row in rows:
        try:
            rate = float(row.rate_vs_base)
        except Exception:
            continue
        if rate <= 0:
            continue
        rates[str(row.currency)] = rate
        versions.add(str(row.rate_version or ""))
        bases.add(str(row.base_currency or ""))
        snapshot_time = row.provider_timestamp or row.last_updated
        if snapshot_time:
            timestamps.append(get_datetime(snapshot_time))
    if base:
        rates[base] = 1.0
    coherent = bool(base and len(versions) == 1 and "" not in versions and bases == {base})
    as_of = min(timestamps) if timestamps and coherent else None
    version = next(iter(versions)) if coherent else ""
    fresh = bool(as_of and now_datetime() - as_of <= timedelta(hours=settings.fx_max_stale_hours))
    payload = {"base_currency": base, "rate_version": version, "as_of": str(as_of or ""), "rates": rates}
    cache.set_value(key, payload, expires_in_sec=CACHE_TTL)
    return FXSnapshot(base, version, as_of, rates, fresh)


def convert_result(amount: float, from_currency: str, to_currency: str) -> ConversionResult:
    source = str(from_currency or "").strip()
    target = str(to_currency or "").strip()
    snapshot = get_fx_snapshot()
    if source == target and source:
        return convert_amount(amount, source, target, 1.0, 1.0, snapshot.base_currency)
    if not snapshot.fresh:
        return ConversionResult(float(amount or 0), source, target, False, False, None, "FX_RATE_UNAVAILABLE")
    return convert_amount(
        amount,
        source,
        target,
        snapshot.rates.get(source),
        snapshot.rates.get(target),
        snapshot.base_currency,
    )


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    """Compatibility-free convenience: unavailable conversion raises explicitly."""
    result = convert_result(amount, from_currency, to_currency)
    if not result.available:
        raise RuntimeError("FX_RATE_UNAVAILABLE")
    return result.amount
