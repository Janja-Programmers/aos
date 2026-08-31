"""Restore the AOS Short Event idempotency uniqueness index on upgraded sites.

``uq_short_event_key`` was added to ``install_shorts_indexes`` after that patch
had already run on some sites.  Frappe does not rerun completed patches, so a
new schema-only patch is required to converge existing installations.
"""
from __future__ import annotations

from aos.patches.v1_0.install_shorts_indexes import _ensure_index


def execute() -> None:
    _ensure_index(
        "AOS Short Event",
        "uq_short_event_key",
        ("event_key",),
        unique=True,
    )
