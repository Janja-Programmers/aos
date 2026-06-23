import frappe

from aos.api.shared.account_status import get_account_state


def get_user_payload(user_name: str) -> dict:
    """Keep payload light and stable for mobile clients."""
    u = frappe.get_doc("User", user_name)
    state = get_account_state(user_name)

    return {
        "email": u.email,
        "full_name": (u.full_name or u.first_name or "").strip(),
        "enabled": int(u.enabled or 0),
        "account_status": state.get("account_status"),
        "is_deleted": bool(state.get("is_deleted")),
        "can_restore": bool(state.get("can_restore")),
    }
