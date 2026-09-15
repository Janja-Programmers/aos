"""Shared, deterministic currency conversion helpers.

The SQL expressions deliberately return the original numeric value when a
cross-currency conversion cannot be proven from both exchange-rate rows.
"""

from __future__ import annotations

from dataclasses import dataclass


MISSING_EXCHANGE_RATE = "MISSING_EXCHANGE_RATE"


@dataclass(frozen=True)
class ConversionResult:
    amount: float
    display_currency: str
    requested_display_currency: str
    available: bool
    converted: bool
    rate: float | None
    reason: str | None


def convert_amount(
    amount: float,
    source_currency: str,
    target_currency: str,
    source_rate: float | None,
    target_rate: float | None,
    base_currency: str | None = None,
) -> ConversionResult:
    """Convert only when equal currencies or both positive rates are known."""
    amount = float(amount or 0)
    if source_currency == target_currency:
        return ConversionResult(amount, source_currency, target_currency, True, False, 1.0, None)
    effective_source_rate = 1.0 if source_currency == base_currency else source_rate
    effective_target_rate = 1.0 if target_currency == base_currency else target_rate
    if effective_source_rate and effective_target_rate and effective_source_rate > 0 and effective_target_rate > 0:
        rate = float(effective_target_rate) / float(effective_source_rate)
        return ConversionResult(round(amount * rate, 6), target_currency, target_currency, True, True, rate, None)
    return ConversionResult(amount, source_currency, target_currency, False, False, None, MISSING_EXCHANGE_RATE)


def sql_conversion_expressions(
    *,
    amount_sql: str,
    source_currency_sql: str = "a.currency",
    target_param: str = "%(display_currency)s",
    base_param: str = "%(base_currency)s",
    fresh_after_param: str | None = None,
) -> dict[str, str]:
    """Return SQL conversion expressions from one coherent rate snapshot.

    ``fresh_after_param`` should be a bound datetime placeholder on Ads search
    paths. Same-currency prices never need FX. Cross-currency availability fails
    closed if either row is stale or belongs to another base snapshot.
    """
    same = f"{source_currency_sql} = {target_param}"
    source_rate = f"CASE WHEN {source_currency_sql} = {base_param} THEN 1 ELSE er_source.rate_vs_base END"
    target_rate = f"CASE WHEN {target_param} = {base_param} THEN 1 ELSE er_target.rate_vs_base END"
    source_meta = f"({source_currency_sql} = {base_param} OR (er_source.base_currency = {base_param} AND er_source.rate_version <> ''))"
    target_meta = f"({target_param} = {base_param} OR (er_target.base_currency = {base_param} AND er_target.rate_version <> ''))"
    same_version = f"({source_currency_sql} = {base_param} OR {target_param} = {base_param} OR er_source.rate_version = er_target.rate_version)"
    freshness = "1=1"
    if fresh_after_param:
        freshness = (
            f"({source_currency_sql} = {base_param} OR er_source.provider_timestamp >= {fresh_after_param}) "
            f"AND ({target_param} = {base_param} OR er_target.provider_timestamp >= {fresh_after_param})"
        )
    rates = (
        f"({source_rate}) IS NOT NULL AND ({source_rate}) > 0 AND "
        f"({target_rate}) IS NOT NULL AND ({target_rate}) > 0 AND "
        f"{source_meta} AND {target_meta} AND {same_version} AND ({freshness})"
    )
    rate = f"CASE WHEN {same} THEN 1 WHEN {rates} THEN ({target_rate}) / ({source_rate}) ELSE NULL END"
    amount = f"CASE WHEN {same} THEN ({amount_sql}) WHEN {rates} THEN ({amount_sql}) * (({target_rate}) / ({source_rate})) ELSE ({amount_sql}) END"
    currency = f"CASE WHEN {same} OR ({rates}) THEN {target_param} ELSE {source_currency_sql} END"
    available = f"CASE WHEN {same} OR ({rates}) THEN 1 ELSE 0 END"
    return {"amount": amount, "currency": currency, "available": available, "rate": rate}
