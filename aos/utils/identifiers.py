"""Collision-resistant identifiers for AOS-owned database records."""

from __future__ import annotations

from uuid import uuid4


def new_prefixed_name(prefix: str) -> str:
    """Return a multi-node-safe opaque Frappe document name.

    UUIDv4 provides 122 random bits and requires no shared counter, Redis lock,
    process-local state, or naming-series row. The database primary key remains
    the final uniqueness boundary.
    """

    clean = str(prefix or "").strip().strip("-")
    if not clean:
        raise ValueError("Identifier prefix is required.")
    return f"{clean}-{uuid4().hex}"
