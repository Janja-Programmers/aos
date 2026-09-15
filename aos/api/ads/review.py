"""Frontend-consumed manual review action for authorized reviewers."""
from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.review import review_ad
from aos.services.ads.validation import ensure_known_fields, normalize_identifier


def review_ad_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(key=f"aos:ads:manual-review:{user}", ttl_seconds=60, limit=60, message="Too many requests.")
    if limited:
        return limited

    def _review():
        ensure_known_fields(kwargs, {"ad_id", "decision", "reason", "version"})
        public_id = normalize_identifier(kwargs.get("ad_id"), field="ad_id", required=True)
        doc = review_ad(
            public_id=public_id,
            decision=kwargs.get("decision"),
            reason=kwargs.get("reason") or "",
            version=kwargs.get("version"),
            reviewer=user,
        )
        return ok(
            "Ad reviewed.",
            data={"id": doc.public_id, "status": doc.status, "version": str(doc.modified), "review_result": doc.review_result},
        )

    return run_ads_api(_review, fallback="Failed to review ad.", log_title="AOS Manual Ad Review Failed", transactional=True)
