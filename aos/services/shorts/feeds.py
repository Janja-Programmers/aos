"""Feed ranking helpers where external ranking is advisory, never exhaustive."""

from __future__ import annotations

from typing import Iterable

import frappe

from aos.api.shared.sql_safety import clean_safe_docnames


def candidate_boost_sql(short_ids: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """Build a parameterized CASE expression for first-page candidate boosting."""
    candidates = clean_safe_docnames(list(short_ids or []))[:100]
    if not candidates:
        return "0", ()
    clauses = []
    params: list[str] = []
    score = len(candidates)
    for short_id in candidates:
        clauses.append("WHEN s.name = %s THEN %s")
        params.extend([short_id, str(score)])
        score -= 1
    # MariaDB accepts numeric strings as bound values; preserving one tuple type
    # keeps static SQL tests simple and values fully parameterized.
    return f"CASE {' '.join(clauses)} ELSE 0 END", tuple(params)


def safe_candidate_ids(short_ids: Iterable[str]) -> list[str]:
    return clean_safe_docnames(list(short_ids or []))[:100]
