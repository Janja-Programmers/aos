import frappe


def get_user_payload(user_name: str) -> dict:
    """Keep payload light and stable for mobile clients."""
    u = frappe.get_doc("User", user_name)
    return {
        "email": u.email,
        "full_name": (u.full_name or u.first_name or "").strip(),
        "enabled": int(u.enabled or 0),
    }
