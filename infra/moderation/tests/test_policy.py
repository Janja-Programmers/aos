from __future__ import annotations

from app.policy import evaluate_policy
from app.text_detector import detect_text


def _signal(category: str, confidence: float, severity: str = "high"):
    return {"category": category, "confidence": confidence, "severity": severity, "source": "test"}


def test_high_confidence_severe_signal_rejects_even_with_safe_context():
    result = evaluate_policy([_signal("pornography", 0.98, "critical")])
    assert result.decision == "reject"
    assert result.categories == ("pornography",)


def test_borderline_signal_requires_manual_review():
    result = evaluate_policy([_signal("weapons", 0.62)])
    assert result.decision == "review"


def test_missing_required_evidence_is_never_safe():
    result = evaluate_policy([], missing_required_evidence=["video_visual"])
    assert result.decision == "review"


def test_detector_failure_is_never_safe():
    result = evaluate_policy([], detector_failures=["image"])
    assert result.decision == "review"


def test_all_clear_allows():
    assert evaluate_policy([]).decision == "allow"


def test_text_obfuscation_and_false_positive_boundaries():
    signals, failures = detect_text([{"field": "caption", "text": "f.u.c.k this"}], max_chars=500)
    assert not failures
    assert any(item["category"] == "profanity" for item in signals)

    innocent, _ = detect_text([{"field": "caption", "text": "Scunthorpe classic furniture"}], max_chars=500)
    assert not any(item["category"] == "profanity" for item in innocent)


def test_spacing_evasion_is_detected():
    signals, _ = detect_text([{"field": "caption", "text": "f u c k"}], max_chars=500)
    assert any(item["category"] == "profanity" for item in signals)
