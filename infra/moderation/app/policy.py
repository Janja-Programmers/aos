from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Iterable

POLICY_VERSION = os.getenv("MODERATION_POLICY_VERSION", "aos-safety-2026-09-26-v6").strip() or "aos-safety-2026-09-26-v6"

CATEGORIES = (
    "sexual_explicit",
    "pornography",
    "nudity",
    "weapons",
    "violence",
    "graphic_violence",
    "profanity",
    "hate",
    "harassment",
    "threats",
    "self_harm",
    "illegal_goods",
    "dangerous_content",
    "drugs",
    "spam",
    "scam",
    "fraud",
    "other",
)

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Policy thresholds are centralized here. A detector emits evidence; it never owns
# the final AOS decision. Thresholds are intentionally conservative and can be
# changed only by changing POLICY_VERSION.
_REJECT_THRESHOLD = {
    "sexual_explicit": 0.90,
    "pornography": 0.90,
    "nudity": 0.94,
    "weapons": 0.93,
    "violence": 0.94,
    "graphic_violence": 0.86,
    "profanity": 0.96,
    "hate": 0.90,
    "harassment": 0.94,
    "threats": 0.88,
    "self_harm": 0.92,
    "illegal_goods": 0.91,
    "dangerous_content": 0.91,
    "drugs": 0.93,
    "spam": 0.97,
    "scam": 0.92,
    "fraud": 0.92,
    "other": 0.97,
}
_REVIEW_THRESHOLD = {category: 0.45 for category in CATEGORIES}
_REVIEW_THRESHOLD.update({"nudity": 0.40, "weapons": 0.40, "graphic_violence": 0.35, "threats": 0.35})

# Zero-shot/NLI semantic scores are useful contextual evidence but are not
# calibrated probabilities. Moderate uncorroborated semantic guesses remain in
# the audit payload without creating review load. Strong semantic evidence can
# still reject, and cross-modal/same-category corroboration lowers the review
# threshold back to the category policy threshold.
_SEMANTIC_UNCORROBORATED_REVIEW_THRESHOLD = 0.75
_SEMANTIC_REJECT_FLOOR = 0.92
_SEMANTIC_CORROBORATION_MIN_CONFIDENCE = 0.20

# OpenCLIP zero-shot scores are relative evidence, not calibrated probabilities.
# An unsafe class narrowly winning a near-uniform softmax is not sufficient to
# create manual-review load. Vision uncertainty is policy-relevant only when the
# unsafe-leading score is materially above the ten-way baseline and separates
# from the runner-up by a meaningful margin.
_VISION_UNCERTAINTY_MIN_CONFIDENCE = 0.20
_VISION_UNCERTAINTY_MIN_MARGIN = 0.03


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    risk_score: float
    reasons: tuple[str, ...]
    categories: tuple[str, ...]


def _confidence(signal: dict[str, Any]) -> float:
    try:
        return max(0.0, min(float(signal.get("confidence") or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _category(signal: dict[str, Any]) -> str:
    value = str(signal.get("category") or "other").strip().lower()
    return value if value in CATEGORIES else "other"


def _severity(signal: dict[str, Any]) -> str:
    value = str(signal.get("severity") or "medium").strip().lower()
    return value if value in SEVERITY_ORDER else "medium"


def _is_semantic_text_signal(signal: dict[str, Any]) -> bool:
    return str(signal.get("reason") or "").strip() == "semantic_text_classifier"


def evaluate_policy(
    signals: Iterable[dict[str, Any]],
    *,
    missing_required_evidence: Iterable[str] = (),
    detector_failures: Iterable[str] = (),
    review_reasons: Iterable[str] = (),
    vision_uncertainty: Iterable[dict[str, Any]] = (),
) -> PolicyResult:
    normalized = [signal for signal in signals if isinstance(signal, dict)]
    missing = tuple(sorted({str(item).strip() for item in missing_required_evidence if str(item).strip()}))
    failures = tuple(sorted({str(item).strip() for item in detector_failures if str(item).strip()}))
    explicit_review = tuple(sorted({str(item).strip() for item in review_reasons if str(item).strip()}))
    vision_ambiguity = [item for item in vision_uncertainty if isinstance(item, dict)]

    corroborated_categories = {
        _category(signal)
        for signal in normalized
        if not _is_semantic_text_signal(signal)
        and _confidence(signal) >= _SEMANTIC_CORROBORATION_MIN_CONFIDENCE
    }

    highest = 0.0
    reject: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for signal in normalized:
        category = _category(signal)
        confidence = _confidence(signal)
        severity = _severity(signal)
        semantic = _is_semantic_text_signal(signal)
        reject_threshold = _REJECT_THRESHOLD[category]
        review_threshold = _REVIEW_THRESHOLD[category]
        if semantic:
            reject_threshold = max(reject_threshold, _SEMANTIC_REJECT_FLOOR)
            if category not in corroborated_categories:
                review_threshold = max(review_threshold, _SEMANTIC_UNCORROBORATED_REVIEW_THRESHOLD)

        actionable = False
        if confidence >= reject_threshold and SEVERITY_ORDER[severity] >= SEVERITY_ORDER["high"]:
            reject.append(signal)
            actionable = True
        elif confidence >= review_threshold:
            review.append(signal)
            actionable = True
        elif not semantic:
            # Non-semantic evidence (deterministic rules / calibrated adapters)
            # still contributes to the policy risk score even when sub-threshold.
            actionable = True
        if actionable:
            highest = max(highest, confidence)

    if reject:
        categories = tuple(sorted({_category(signal) for signal in reject}))
        return PolicyResult(
            decision="reject",
            risk_score=highest,
            reasons=tuple(f"high-confidence {category} violation" for category in categories),
            categories=categories,
        )

    if failures or missing:
        reasons = tuple([*(f"detector failure: {item}" for item in failures), *(f"missing evidence: {item}" for item in missing)])
        categories = tuple(sorted({_category(signal) for signal in review}))
        return PolicyResult(decision="review", risk_score=max(highest, 0.5), reasons=reasons, categories=categories)

    meaningful_vision_uncertainty: list[dict[str, Any]] = []
    for item in vision_ambiguity:
        category = _category({"category": item.get("top_category")})
        if category == "other" and str(item.get("top_category") or "").strip().lower() not in CATEGORIES:
            continue
        try:
            top_confidence = max(0.0, min(float(item.get("top_confidence") or 0.0), 1.0))
            margin = max(0.0, min(float(item.get("margin") or 0.0), 1.0))
        except (TypeError, ValueError):
            continue
        if top_confidence >= _VISION_UNCERTAINTY_MIN_CONFIDENCE and margin >= _VISION_UNCERTAINTY_MIN_MARGIN:
            meaningful_vision_uncertainty.append({**item, "top_category": category, "top_confidence": top_confidence, "margin": margin})

    if meaningful_vision_uncertainty:
        categories = tuple(sorted({str(item["top_category"]) for item in meaningful_vision_uncertainty}))
        risk_score = max(highest, max(float(item["top_confidence"]) for item in meaningful_vision_uncertainty))
        reasons = tuple(f"vision uncertainty: {category} meaningfully outranked safe" for category in categories)
        return PolicyResult(decision="review", risk_score=risk_score, reasons=reasons, categories=categories)

    if explicit_review:
        categories = tuple(sorted({_category(signal) for signal in review}))
        return PolicyResult(decision="review", risk_score=highest, reasons=explicit_review, categories=categories)

    if review:
        categories = tuple(sorted({_category(signal) for signal in review}))
        return PolicyResult(
            decision="review",
            risk_score=highest,
            reasons=tuple(f"borderline {category} signal" for category in categories),
            categories=categories,
        )

    return PolicyResult(decision="allow", risk_score=highest, reasons=(), categories=())
