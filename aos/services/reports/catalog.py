"""Canonical initial Report Reason catalog for fresh AOS sites."""

from __future__ import annotations

import frappe

from .constants import (
    REPORT_TARGET_AD,
    REPORT_TARGET_REVIEW,
    REPORT_TARGET_SHORT,
    REPORT_TARGET_USER,
)

# These labels reflect the report reasons already present in current AOS client
# behavior. Stable machine ids replace labels as durable API identity. The two
# historical scam labels are intentionally consolidated into ``scam_fraud``.
CANONICAL_REPORT_REASONS: tuple[dict[str, object], ...] = (
    {
        "reason_id": "spam",
        "label": "Spam",
        "description": "Repeated, unsolicited, or disruptive behavior/content.",
        "icon_key": "spam",
        "sort_order": 10,
        "targets": (REPORT_TARGET_USER, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW),
    },
    {
        "reason_id": "harassment_abuse",
        "label": "Harassment or abuse",
        "description": "Harassment, threats, bullying, or abusive behavior.",
        "icon_key": "person_off",
        "sort_order": 20,
        "targets": (REPORT_TARGET_USER, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW),
    },
    {
        "reason_id": "nudity_sexual_content",
        "label": "Nudity or sexual content",
        "description": "Sexual or nudity content that may violate platform rules.",
        "icon_key": "visibility_off",
        "sort_order": 30,
        "targets": (REPORT_TARGET_SHORT,),
    },
    {
        "reason_id": "violence_dangerous_content",
        "label": "Violence or dangerous content",
        "description": "Violent, dangerous, or harmful content.",
        "icon_key": "warning",
        "sort_order": 40,
        "targets": (REPORT_TARGET_SHORT,),
    },
    {
        "reason_id": "scam_fraud",
        "label": "Scam or fraud",
        "description": "Suspected scam, fraud, or deceptive behavior.",
        "icon_key": "shield",
        "sort_order": 50,
        "targets": (REPORT_TARGET_USER, REPORT_TARGET_AD, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW),
    },
    {
        "reason_id": "misleading_description",
        "label": "Misleading or inaccurate description",
        "description": "The listing description is materially inaccurate or misleading.",
        "icon_key": "description",
        "sort_order": 60,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "prohibited_restricted_item",
        "label": "Prohibited or restricted item",
        "description": "The listing appears to offer an item that is prohibited or restricted.",
        "icon_key": "block",
        "sort_order": 70,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "inappropriate_content",
        "label": "Inappropriate content",
        "description": "Content appears inappropriate for the platform.",
        "icon_key": "report",
        "sort_order": 80,
        "targets": (REPORT_TARGET_AD, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW),
    },
    {
        "reason_id": "wrong_category",
        "label": "Wrong category",
        "description": "The listing is placed in an incorrect category.",
        "icon_key": "category",
        "sort_order": 90,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "misleading_pricing",
        "label": "Wrong or misleading pricing",
        "description": "The listing price or pricing terms appear misleading.",
        "icon_key": "pricing",
        "sort_order": 100,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "duplicate_ad",
        "label": "Duplicate ad",
        "description": "The same listing appears to have been posted more than once.",
        "icon_key": "content_copy",
        "sort_order": 110,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "counterfeit_product",
        "label": "Counterfeit or fake product",
        "description": "The listed product appears counterfeit or falsely represented as genuine.",
        "icon_key": "gpp_bad",
        "sort_order": 120,
        "targets": (REPORT_TARGET_AD,),
    },
    {
        "reason_id": "other",
        "label": "Other",
        "description": "Another issue not covered by the available reasons.",
        "icon_key": "more_horiz",
        "sort_order": 999,
        "targets": (REPORT_TARGET_USER, REPORT_TARGET_AD, REPORT_TARGET_SHORT, REPORT_TARGET_REVIEW),
    },
)


def install_canonical_report_reasons() -> None:
    """Create missing canonical reasons without overwriting operator choices.

    Existing canonical rows are intentionally left unchanged so operators may
    adjust labels/descriptions/order or disable a reason after installation.
    """

    for item in CANONICAL_REPORT_REASONS:
        reason_id = str(item["reason_id"])
        if frappe.db.exists("AOS Report Reason", reason_id):
            continue
        doc = frappe.new_doc("AOS Report Reason")
        doc.reason_id = reason_id
        doc.label = item["label"]
        doc.description = item["description"]
        doc.icon_key = item["icon_key"]
        doc.sort_order = int(item["sort_order"])
        doc.is_enabled = 1
        for target_type in item["targets"]:
            doc.append("allowed_targets", {"target_type": target_type})
        doc.insert(ignore_permissions=True)
