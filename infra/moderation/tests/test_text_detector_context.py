from __future__ import annotations

from app.text_detector import detect_text


def _signals(text: str):
    signals, failures = detect_text([{"field": "comment", "text": text}], max_chars=6000)
    assert failures == []
    return signals


def test_pornography_solicitation_is_rejected_evidence_without_blanket_topic_matching():
    cases = (
        "Dm for porno video",
        "I have porn videos for sale",
        "message me for pornography content",
    )
    for text in cases:
        signals = _signals(text)
        porn = [row for row in signals if row.get("category") == "pornography"]
        assert porn, text
        assert max(float(row["confidence"]) for row in porn) >= 0.97
        assert all(row.get("detector_version") == "5" for row in porn)


def test_informational_pornography_and_name_context_do_not_trigger_deterministic_rule():
    for text in (
        "This documentary discusses pornography and internet safety.",
        "The Dick Van Dyke show is a classic television series.",
    ):
        assert not [row for row in _signals(text) if row.get("category") == "pornography"], text
