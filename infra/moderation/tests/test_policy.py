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




def test_profanity_inflections_reject_without_substring_matching():
    signals, failures = detect_text(
        [{"field": "comment", "text": "you are a fucking scammer"}],
        max_chars=500,
    )
    assert not failures
    profanity = [item for item in signals if item["category"] == "profanity"]
    assert profanity
    assert profanity[0]["detector_version"] == "6"
    assert profanity[0]["evidence"] == ["fuck-family"]
    assert evaluate_policy(signals).decision == "reject"

    for value in ("this is fucked", "fucker", "motherfuckers", "cunts", "f.u.c.k.i.n.g"):
        variant_signals, _ = detect_text([{"field": "comment", "text": value}], max_chars=500)
        assert any(item["category"] == "profanity" for item in variant_signals), value

    for value in ("Scunthorpe classic furniture", "firetruck", "motherhood"):
        innocent, _ = detect_text([{"field": "comment", "text": value}], max_chars=500)
        assert not any(item["category"] == "profanity" for item in innocent), value


def test_near_uniform_vision_tie_does_not_force_manual_review():
    result = evaluate_policy(
        [{"category": "nudity", "confidence": 0.132396, "severity": "medium", "source": "image"}],
        vision_uncertainty=[{
            "top_category": "nudity",
            "top_confidence": 0.132396,
            "safe_confidence": 0.128804,
            "margin": 0.003592,
        }],
    )
    assert result.decision == "allow"
    assert result.risk_score == 0.132396


def test_meaningful_unsafe_leading_vision_uncertainty_requires_review():
    result = evaluate_policy(
        [{"category": "weapons", "confidence": 0.24, "severity": "medium", "source": "image"}],
        vision_uncertainty=[{
            "top_category": "weapons",
            "top_confidence": 0.24,
            "safe_confidence": 0.18,
            "margin": 0.06,
        }],
    )
    assert result.decision == "review"
    assert result.reasons == ("vision uncertainty: weapons meaningfully outranked safe",)
    assert result.categories == ("weapons",)


def test_contextual_drug_transactions_reject_without_banning_discussion():
    for value in (
        "I sell illegal drugs",
        "DM for drugs",
        "cocaine available, message me",
        "order heroin",
        "weed for sale",
    ):
        signals, failures = detect_text([{"field": "comment", "text": value}], max_chars=500)
        assert not failures
        drug_signals = [item for item in signals if item["category"] == "drugs"]
        assert drug_signals, value
        assert drug_signals[0]["detector_version"] == "6"
        assert evaluate_policy(signals).decision == "reject", value

    for value in (
        "Illegal drugs are harmful",
        "This documentary discusses the drug trade",
        "Do not buy illegal drugs",
        "The pharmacy delivered my prescription medicine",
        "I pulled weeds from the garden",
    ):
        signals, failures = detect_text([{"field": "comment", "text": value}], max_chars=500)
        assert not failures
        contextual = [item for item in signals if item.get("reason") == "controlled-drug transaction or solicitation"]
        assert not contextual, value


def test_uncorroborated_moderate_semantic_noise_is_audit_only():
    result = evaluate_policy([
        {"category": "pornography", "confidence": 0.569405, "severity": "critical", "source": "text", "reason": "semantic_text_classifier"},
        {"category": "threats", "confidence": 0.569245, "severity": "critical", "source": "text", "reason": "semantic_text_classifier"},
        {"category": "drugs", "confidence": 0.489124, "severity": "high", "source": "text", "reason": "semantic_text_classifier"},
    ])
    assert result.decision == "allow"
    assert result.risk_score == 0.0
    assert result.categories == ()


def test_strong_semantic_evidence_still_rejects():
    result = evaluate_policy([
        {"category": "pornography", "confidence": 0.97, "severity": "critical", "source": "text", "reason": "semantic_text_classifier"},
    ])
    assert result.decision == "reject"
    assert result.risk_score == 0.97
    assert result.categories == ("pornography",)


def test_semantic_evidence_can_review_when_same_category_is_corroborated():
    result = evaluate_policy([
        {"category": "weapons", "confidence": 0.60, "severity": "high", "source": "text", "reason": "semantic_text_classifier"},
        {"category": "weapons", "confidence": 0.24, "severity": "medium", "source": "image", "reason": "vision_top_category"},
    ])
    assert result.decision == "review"
    assert result.categories == ("weapons",)
    assert result.risk_score == 0.60
