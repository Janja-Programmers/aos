"""Automatic Shorts content-mode classification.

AOS has four canonical modes: ``shop``, ``geo``, ``vibes`` and ``learn``.
``all`` is a feed aggregation and is never persisted as a Short mode.

The video companion supplies visual OpenCLIP scores.  At publish time those
scores are fused with bounded caption/hashtag signals and trusted commerce
context.  Creator-supplied ``content_mode`` values are accepted only for legacy
transport compatibility and are deliberately not authoritative.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from frappe.utils import now_datetime

from aos.services.shorts.constants import (
    SHORT_CONTENT_MODE_GEO,
    SHORT_CONTENT_MODE_LEARN,
    SHORT_CONTENT_MODE_SHOP,
    SHORT_CONTENT_MODE_VIBES,
    VALID_SHORT_CONTENT_MODES,
)

CLASSIFICATION_MODEL = "aos-short-fusion"
CLASSIFICATION_MODEL_VERSION = "1"
CLASSIFICATION_FALLBACK_MODE = SHORT_CONTENT_MODE_VIBES
CLASSIFICATION_VISUAL_WEIGHT = 0.72
CLASSIFICATION_TEXT_WEIGHT = 0.28

_NON_SHOP_MODES = (
    SHORT_CONTENT_MODE_GEO,
    SHORT_CONTENT_MODE_VIBES,
    SHORT_CONTENT_MODE_LEARN,
)
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.IGNORECASE)

# Phrase/token lists are intentionally bounded and versioned in code.  They are
# a resilient fallback and caption complement, not a substitute for visual AI.
_TEXT_SIGNALS: dict[str, tuple[str, ...]] = {
    SHORT_CONTENT_MODE_SHOP: (
        "buy", "sale", "selling", "price", "order", "shop", "store",
        "product", "products", "discount", "offer", "delivery", "stock",
        "available", "unboxing", "review", "seller", "marketplace",
    ),
    SHORT_CONTENT_MODE_GEO: (
        "travel", "trip", "tour", "destination", "location", "route", "road",
        "street", "city", "town", "village", "county", "country", "beach",
        "mountain", "park", "landmark", "scenery", "nairobi", "mombasa",
        "kisumu", "nakuru", "kenya", "africa", "map", "directions",
    ),
    SHORT_CONTENT_MODE_VIBES: (
        "dance", "dancing", "music", "song", "comedy", "funny", "challenge",
        "trend", "trending", "lifestyle", "fashion", "beauty", "party",
        "vlog", "vibes", "entertainment", "performance", "celebration",
        "meme", "skit", "fun", "viral",
    ),
    SHORT_CONTENT_MODE_LEARN: (
        "learn", "lesson", "tutorial", "education", "educational", "study",
        "explain", "explained", "howto", "guide", "tips", "teach", "teacher",
        "math", "maths", "mathematics", "algebra", "geometry", "calculus",
        "science", "physics", "chemistry", "biology", "stem", "engineering",
        "technology", "coding", "programming", "business", "finance", "skill",
        "course", "exam", "revision", "history", "language",
    ),
}

_PHRASE_SIGNALS: dict[str, tuple[str, ...]] = {
    SHORT_CONTENT_MODE_SHOP: ("for sale", "how much", "place your order", "in stock"),
    SHORT_CONTENT_MODE_GEO: ("how to get", "travel guide", "things to do", "visit kenya"),
    SHORT_CONTENT_MODE_VIBES: ("dance challenge", "music video", "day in my life"),
    SHORT_CONTENT_MODE_LEARN: ("how to", "step by step", "did you know", "solve this", "what is"),
}


@dataclass(frozen=True)
class ClassificationResult:
    mode: str
    confidence: float
    scores: dict[str, float]
    status: str
    source: str
    model: str = CLASSIFICATION_MODEL
    model_version: str = CLASSIFICATION_MODEL_VERSION


def _finite_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return max(0.0, min(score, 1.0))


def normalize_scores(raw: object) -> dict[str, float]:
    """Return a complete normalized four-mode score map."""
    mapping = raw if isinstance(raw, Mapping) else {}
    scores = {mode: _finite_score(mapping.get(mode)) for mode in VALID_SHORT_CONTENT_MODES}
    total = sum(scores.values())
    if total <= 0:
        return {mode: 0.0 for mode in VALID_SHORT_CONTENT_MODES}
    return {mode: round(value / total, 6) for mode, value in scores.items()}


def _parse_json_mapping(value: object) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
    return normalize_scores(value)


def _text_scores(caption: str, hashtags: Iterable[object]) -> dict[str, float]:
    hashtag_text = " ".join(str(tag or "") for tag in hashtags or [])
    text = f"{caption or ''} {hashtag_text}".strip().lower()
    tokens = _TOKEN_RE.findall(text)
    token_set = set(tokens)
    raw = {mode: 0.0 for mode in VALID_SHORT_CONTENT_MODES}

    for mode, signals in _TEXT_SIGNALS.items():
        raw[mode] += sum(1.0 for signal in signals if signal in token_set)
    for mode, phrases in _PHRASE_SIGNALS.items():
        raw[mode] += sum(2.0 for phrase in phrases if phrase in text)

    # Hashtags are deliberate metadata and receive a modest additional weight.
    for tag in hashtags or []:
        clean = str(tag or "").strip().lstrip("#").lower()
        for mode, signals in _TEXT_SIGNALS.items():
            if clean and clean in signals:
                raw[mode] += 1.5

    total = sum(raw.values())
    if total <= 0:
        return {mode: 0.0 for mode in VALID_SHORT_CONTENT_MODES}
    return {mode: round(value / total, 6) for mode, value in raw.items()}


def _choose(scores: Mapping[str, float], *, allow_shop: bool) -> tuple[str, float, dict[str, float]]:
    candidates = tuple(VALID_SHORT_CONTENT_MODES) if allow_shop else _NON_SHOP_MODES
    filtered = {mode: _finite_score(scores.get(mode)) for mode in candidates}
    total = sum(filtered.values())
    if total <= 0:
        fallback_scores = {mode: 0.0 for mode in VALID_SHORT_CONTENT_MODES}
        fallback_scores[CLASSIFICATION_FALLBACK_MODE] = 1.0
        return CLASSIFICATION_FALLBACK_MODE, 0.0, fallback_scores

    normalized_candidates = {mode: value / total for mode, value in filtered.items()}
    mode = max(candidates, key=lambda item: (normalized_candidates[item], item == CLASSIFICATION_FALLBACK_MODE))
    complete = {item: 0.0 for item in VALID_SHORT_CONTENT_MODES}
    complete.update({item: round(value, 6) for item, value in normalized_candidates.items()})
    return mode, round(normalized_candidates[mode], 6), complete


def classify_for_publish(
    *,
    visual_scores: object,
    caption: str,
    hashtags: Iterable[object],
    has_shop_context: bool,
    legacy_content_mode: object | None = None,
) -> ClassificationResult:
    """Classify a Short for publication.

    ``legacy_content_mode`` is intentionally ignored.  It remains in the
    signature to make that compatibility decision explicit and testable.
    A validated active ad is authoritative commerce context and yields ``shop``.
    """
    del legacy_content_mode

    if has_shop_context:
        return ClassificationResult(
            mode=SHORT_CONTENT_MODE_SHOP,
            confidence=1.0,
            scores={
                SHORT_CONTENT_MODE_SHOP: 1.0,
                SHORT_CONTENT_MODE_GEO: 0.0,
                SHORT_CONTENT_MODE_VIBES: 0.0,
                SHORT_CONTENT_MODE_LEARN: 0.0,
            },
            status="ready",
            source="commerce_context",
        )

    visual = _parse_json_mapping(visual_scores)
    text = _text_scores(caption, hashtags)
    visual_available = sum(visual.values()) > 0
    text_available = sum(text.values()) > 0

    if visual_available and text_available:
        fused = {
            mode: (visual[mode] * CLASSIFICATION_VISUAL_WEIGHT)
            + (text[mode] * CLASSIFICATION_TEXT_WEIGHT)
            for mode in VALID_SHORT_CONTENT_MODES
        }
        source = "automatic_visual_text"
    elif visual_available:
        fused = visual
        source = "automatic_visual"
    elif text_available:
        fused = text
        source = "automatic_text"
    else:
        fused = {}
        source = "fallback"

    mode, confidence, scores = _choose(fused, allow_shop=False)
    return ClassificationResult(
        mode=mode,
        confidence=confidence,
        scores=scores,
        status="ready" if source != "fallback" else "fallback",
        source=source,
    )


def apply_visual_result(short: Any, payload: object) -> ClassificationResult:
    """Validate and persist the non-authoritative visual classification result."""
    data = payload if isinstance(payload, Mapping) else {}
    scores = normalize_scores(data.get("scores"))
    supplied_status = str(data.get("status") or "").strip().lower()
    visual_available = sum(scores.values()) > 0 and supplied_status == "ready"

    if visual_available:
        mode, confidence, complete_scores = _choose(scores, allow_shop=False)
        result = ClassificationResult(
            mode=mode,
            confidence=confidence,
            scores=complete_scores,
            status="visual_ready",
            source="automatic_visual",
            model=str(data.get("model") or "openclip")[:140],
            model_version=str(data.get("model_version") or "1")[:140],
        )
    else:
        result = ClassificationResult(
            mode=CLASSIFICATION_FALLBACK_MODE,
            confidence=0.0,
            scores={mode: 0.0 for mode in VALID_SHORT_CONTENT_MODES},
            status="fallback",
            source="fallback",
        )

    short.classification_visual_scores = json.dumps(scores, separators=(",", ":"), sort_keys=True)

    # Audio reprocessing and explicit processing retries may run after metadata
    # publication. Refresh visual evidence without overwriting the authoritative
    # fused/commerce/admin result already stored for the published Short.
    current_status = str(getattr(short, "classification_status", None) or "").strip()
    current_source = str(getattr(short, "classification_source", None) or "").strip()
    preserve_final = current_status in {"ready", "legacy"} or current_source in {
        "automatic_text",
        "automatic_visual_text",
        "commerce_context",
        "admin_override",
        "legacy",
    }
    if preserve_final and getattr(short, "content_mode", None):
        return result

    short.content_mode = result.mode
    short.classification_status = result.status
    short.classification_source = result.source
    short.classification_confidence = result.confidence
    short.classification_scores = json.dumps(result.scores, separators=(",", ":"), sort_keys=True)
    short.classification_model = result.model
    short.classification_model_version = result.model_version
    short.classified_at = now_datetime()
    return result


def apply_publish_result(short: Any, result: ClassificationResult) -> None:
    short.content_mode = result.mode
    short.classification_status = result.status
    short.classification_source = result.source
    short.classification_confidence = result.confidence
    short.classification_scores = json.dumps(result.scores, separators=(",", ":"), sort_keys=True)
    short.classification_model = result.model
    short.classification_model_version = result.model_version
    short.classified_at = now_datetime()


def public_classification(short_or_row: Any) -> dict[str, Any]:
    getter = short_or_row.get if isinstance(short_or_row, Mapping) else lambda key, default=None: getattr(short_or_row, key, default)
    return {
        "status": str(getter("classification_status", "") or "fallback"),
        "source": str(getter("classification_source", "") or "fallback"),
        "confidence": round(_finite_score(getter("classification_confidence", 0.0)), 6),
        "model_version": str(getter("classification_model_version", "") or "") or None,
    }
