"""Final-stage geographic reranking policy.

Geography never generates candidates and never changes eligibility.  It is the
final stable reranking stage: exact location, same country, remaining global.
The input order is therefore preserved inside each geographic bucket.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeVar

T = TypeVar("T")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def geographic_bucket(*, item_country: Any, item_location: Any, country: Any, location: Any) -> int:
    wanted_country = _clean(country)
    wanted_location = _clean(location)
    actual_country = _clean(item_country)
    actual_location = _clean(item_location)
    if wanted_country and wanted_location and actual_country == wanted_country and actual_location == wanted_location:
        return 0
    if wanted_country and actual_country == wanted_country:
        return 1
    return 2


def stable_geographic_rerank(
    items: Iterable[T], *, country: Any, location: Any, country_key: str = "country", location_key: str = "location"
) -> list[T]:
    indexed = list(enumerate(items))

    def get(item: T, key: str) -> Any:
        if isinstance(item, Mapping):
            return item.get(key)
        return getattr(item, key, None)

    indexed.sort(
        key=lambda pair: (
            geographic_bucket(
                item_country=get(pair[1], country_key),
                item_location=get(pair[1], location_key),
                country=country,
                location=location,
            ),
            pair[0],
        )
    )
    return [item for _, item in indexed]


def sql_geographic_bucket(*, table_alias: str = "a", country_param: str = "country", location_param: str = "location") -> str:
    alias = "".join(ch for ch in table_alias if ch.isalnum() or ch == "_") or "a"
    return (
        f"CASE "
        f"WHEN %({country_param})s <> '' AND %({location_param})s <> '' "
        f"AND {alias}.country = %({country_param})s AND {alias}.location = %({location_param})s THEN 0 "
        f"WHEN %({country_param})s <> '' AND {alias}.country = %({country_param})s THEN 1 "
        f"ELSE 2 END"
    )
