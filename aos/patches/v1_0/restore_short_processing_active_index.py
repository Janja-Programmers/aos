"""Restore the Shorts active video-processing uniqueness index.

Changing the internal Video Processing Job naming strategy from a naming series
into hash naming causes Frappe to resynchronise that DocType on existing sites.
The schema sync can remove the manually-installed custom uniqueness index from
the earlier Shorts hardening patch.  That earlier patch is already recorded as
executed, so upgrades need a new schema-only patch to reassert the invariant.

The patch is idempotent and contains no DML.  Existing job names are untouched.
"""
from __future__ import annotations

from aos.patches.v1_0.install_shorts_indexes import _ensure_index


def execute() -> None:
    _ensure_index(
        "AOS Video Processing Job",
        "uq_short_processing_active",
        ("active_key",),
        unique=True,
    )
