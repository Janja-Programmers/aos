"""Focused Accounts data access with bounded projections."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import ACCOUNT_STATUS_ACTIVE, PROFILE_DOCTYPE
from .identity import normalize_public_account_id

_PUBLIC_PROFILE_FIELDS = """
    p.name AS account_id,
    p.user,
    p.display_name,
    p.bio,
    p.profile_image_media,
    COALESCE(NULLIF(p.account_status, ''), %(active)s) AS account_status,
    COALESCE(p.total_followers, 0) AS total_followers,
    COALESCE(p.total_following, 0) AS total_following,
    COALESCE(p.total_friends, 0) AS total_friends,
    COALESCE(p.is_verified, 0) AS is_verified,
    COALESCE(u.enabled, 0) AS enabled
"""

_PRIVATE_PROFILE_FIELDS = """
    p.name AS account_id,
    p.user,
    p.display_name,
    p.legal_name,
    p.bio,
    p.phone,
    p.date_of_birth,
    p.gender,
    p.profile_image_media,
    COALESCE(NULLIF(p.account_status, ''), %(active)s) AS account_status,
    p.deleted_at,
    p.restore_deadline,
    p.purge_status,
    p.purge_started_at,
    p.purge_completed_at,
    COALESCE(p.total_followers, 0) AS total_followers,
    COALESCE(p.total_following, 0) AS total_following,
    COALESCE(p.total_friends, 0) AS total_friends,
    COALESCE(p.is_verified, 0) AS is_verified,
    u.email,
    COALESCE(u.enabled, 0) AS enabled
"""


class AccountRepository:
    """Database projections used by profile read/write paths."""

    @staticmethod
    def account_by_user(user: str):
        user = str(user or "").strip()
        if not user:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT {_PRIVATE_PROFILE_FIELDS}
            FROM `tabAOS Profile` p
            INNER JOIN `tabUser` u ON u.name = p.user
            WHERE p.user = %(user)s
            LIMIT 1
            """,
            {"user": user, "active": ACCOUNT_STATUS_ACTIVE},
            as_dict=True,
        )
        return rows[0] if rows else None

    @staticmethod
    def account_by_public_id(account_id: Any):
        normalized = normalize_public_account_id(account_id)
        if not normalized:
            return None
        rows = frappe.db.sql(
            f"""
            SELECT {_PUBLIC_PROFILE_FIELDS}
            FROM `tabAOS Profile` p
            INNER JOIN `tabUser` u ON u.name = p.user
            WHERE p.name = %(account_id)s
            LIMIT 1
            """,
            {"account_id": normalized, "active": ACCOUNT_STATUS_ACTIVE},
            as_dict=True,
        )
        return rows[0] if rows else None

    @staticmethod
    def lock_profile(user: str):
        """Lock the profile row and return the Frappe document in this transaction."""

        user = str(user or "").strip()
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE user = %s LIMIT 1 FOR UPDATE",
            (user,),
            as_dict=True,
        )
        if not rows:
            return None
        return frappe.get_doc(PROFILE_DOCTYPE, rows[0].name)
