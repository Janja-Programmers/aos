"""Safe serializers for auth/session responses."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.account_status import get_account_state
from aos.services.localization_service import serialize_preference as serialize_localization_preference
from aos.services.user_preference_service import is_country_locked

SAFE_USER_FIELDS = ["name", "email", "full_name", "first_name", "last_name", "user_image", "enabled"]


def _user_doc(user: str):
	return frappe.db.get_value("User", user, SAFE_USER_FIELDS, as_dict=True)


def serialize_auth_user(user: str) -> dict[str, Any]:
	row = _user_doc(user) or {}
	state = get_account_state(user)

	return {
		"id": row.get("name") or user,
		"email": row.get("email") or user,
		"full_name": (row.get("full_name") or row.get("first_name") or "").strip(),
		"first_name": row.get("first_name") or "",
		"last_name": row.get("last_name") or "",
		"user_image": row.get("user_image"),
		"enabled": bool(int(row.get("enabled") or 0)),
		"account_status": state.get("account_status"),
	}


def serialize_preference(user: str) -> dict[str, Any]:
	pref = (
		frappe.db.get_value(
			"AOS User Preference",
			{"user": user},
			["country", "language", "currency"],
			as_dict=True,
		)
		or {}
	)

	if not pref:
		return {}
	return serialize_localization_preference(pref, is_country_locked=is_country_locked(user))


def serialize_roles(user: str) -> list[str]:
	try:
		roles = frappe.get_roles(user) or []
	except Exception:
		roles = []
	return sorted(role for role in roles if role not in {"All", "Guest"})


def serialize_seller_summary(user: str) -> dict[str, Any]:
	seller = frappe.db.get_value(
		"AOS Seller",
		{"user": user},
		["name", "status"],
		as_dict=True,
	)

	if not seller:
		return {"is_seller": False, "seller_id": None, "status": None}

	return {
		"is_seller": seller.get("status") == "Active",
		"seller_id": seller.get("name"),
		"status": seller.get("status"),
	}


def serialize_session(*, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
	payload: dict[str, Any] = {
		"authenticated": True,
		"expires_at": None,
	}
	if include_sid:
		payload["sid"] = sid
	return payload


def serialize_auth_payload(user: str, *, sid: str | None = None, include_sid: bool = False) -> dict[str, Any]:
	return {
		"session": serialize_session(sid=sid, include_sid=include_sid),
		"user": serialize_auth_user(user),
		"preferences": serialize_preference(user),
		"roles": serialize_roles(user),
		"seller": serialize_seller_summary(user),
	}
