from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail

from .constants import REMOVE_BG_LIMIT_PER_MINUTE_PER_USER


BACKGROUND_REMOVAL_UNAVAILABLE_MESSAGE = (
    "Background removal is temporarily unavailable while the AI media service is being upgraded."
)


def remove_background_impl(**kwargs):
    """
    Remove background from an uploaded image.

    Temporary Phase 1 boundary:
    - Background removal must not run AI/ML inside the Frappe business backend.
    - The old rembg/onnxruntime implementation has been removed from backend dependencies.
    - Phase 2 will wire this endpoint to an external AI media/background-removal service.

    The endpoint is kept so existing clients receive a controlled response instead
    of an import/install failure.
    """
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:files:remove_bg:user:{current_user}",
        ttl_seconds=60,
        limit=REMOVE_BG_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    frappe.logger("aos").info(
        "Background removal requested but local backend AI implementation has been removed."
    )

    return fail(
        BACKGROUND_REMOVAL_UNAVAILABLE_MESSAGE,
        code="BACKGROUND_REMOVAL_UNAVAILABLE",
    )
