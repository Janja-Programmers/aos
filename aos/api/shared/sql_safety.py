"""Small helpers for safe SQL fragments.

Use these only for SQL fragments that cannot be represented as query
parameters, such as column identifiers, table aliases, and LIKE wildcard
escaping. Values must still be passed through the database driver's parameter
binding.
"""

from __future__ import annotations

import re
from typing import Iterable


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_DOTTED_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_DOCNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def require_sql_identifier(value: str, *, label: str = "SQL identifier") -> str:
    """Return a safe single SQL identifier or raise ValueError.

    Examples accepted: ``s``, ``creation``, ``active_identity_key``.
    Examples rejected: ``s.name``, ``name desc``, ``name; drop``.
    """

    normalized = str(value or "").strip()
    if not _SQL_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Unsafe {label}.")
    return normalized


def require_dotted_sql_identifier(value: str, *, label: str = "SQL identifier") -> str:
    """Return a safe dotted SQL identifier or raise ValueError.

    Examples accepted: ``s.name``, ``creation``, ``sv.creation``.
    Examples rejected: ``s.name desc``, ``s.name; drop table``.
    """

    normalized = str(value or "").strip()
    if not _SQL_DOTTED_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Unsafe {label}.")
    return normalized


def require_allowed_sql_identifier(
    value: str,
    *,
    allowed: Iterable[str],
    label: str = "SQL identifier",
) -> str:
    """Return a safe identifier that is also in a fixed allowlist."""

    normalized = require_sql_identifier(value, label=label)
    allowed_set = {str(item) for item in allowed}
    if normalized not in allowed_set:
        raise ValueError(f"Unsupported {label}.")
    return normalized


def safe_like_contains(value: object) -> str:
    """Return an escaped LIKE pattern for a contains search.

    The caller must use ``LIKE %s ESCAPE '\\'``.
    """

    return f"%{escape_like_literal(value)}%"


def safe_like_prefix(value: object) -> str:
    """Return an escaped LIKE pattern for a prefix search.

    The caller must use ``LIKE %s ESCAPE '\\'``.
    """

    return f"{escape_like_literal(value)}%"


def escape_like_literal(value: object) -> str:
    """Escape user-controlled LIKE wildcards so input is treated as text."""

    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_safe_docname(value: object) -> bool:
    """Return whether a value is safe to embed after DB escaping in FIELD()/IN().

    Query values should normally be bound as parameters. This helper is for
    hard-to-parameterize ordering fragments that receive candidate IDs from
    trusted/internal services but still deserve a defensive allowlist.
    """

    normalized = str(value or "").strip()
    return bool(normalized and len(normalized) <= 140 and _SAFE_DOCNAME_RE.fullmatch(normalized))


def clean_safe_docnames(values: Iterable[object]) -> list[str]:
    """Return unique safe docnames, preserving input order."""

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = str(value or "").strip()
        if not is_safe_docname(normalized) or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    return cleaned
