from __future__ import annotations

import re
import unicodedata
from typing import Any

_WORD = re.compile(r"[a-z0-9']+")
_SINGLE_LETTER_RUN = re.compile(r"(?<![a-z0-9])(?:[a-z][\W_]+){2,}[a-z](?![a-z0-9])", re.I)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

# These rules are deliberately token/phrase based rather than substring based.
# They are one detector among several and only high-confidence unambiguous rules
# are eligible for automatic rejection.
#
# Strong profanity needs bounded inflection handling: exact-token matching of only
# ``fuck`` missed ordinary forms such as ``fucking``/``fucked``.  Keep these as
# full-token regex families so innocent words are never matched by substring.
_TOKEN_FAMILY_RULES: tuple[tuple[str, str, str, float, tuple[tuple[re.Pattern[str], str], ...]], ...] = (
    (
        "profanity",
        "high",
        "strong profanity",
        0.98,
        (
            (re.compile(r"^fuck(?:s|ed|er|ers|ing|in)?$"), "fuck-family"),
            (re.compile(r"^motherfuck(?:er|ers|ing)?$"), "motherfuck-family"),
            (re.compile(r"^cunt(?:s)?$"), "cunt-family"),
        ),
    ),
)

_DRUG_TERM = r"(?:illegal\s+)?(?:drugs?|cocaine|heroin|meth(?:amphetamine)?|fentanyl|mdma|ecstasy|narcotics?|marijuana|cannabis|weed)"
_DRUG_SOLICITATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"\b(?:i|we)\s+(?:sell|supply|deliver|stock)\b(?:\s+[a-z0-9']+){{0,5}}\s+\b{_DRUG_TERM}\b"), "seller-offer"),
    (re.compile(rf"\b{_DRUG_TERM}\b(?:\s+[a-z0-9']+){{0,4}}\s+\b(?:for\s+sale|available|in\s+stock)\b"), "availability"),
    (re.compile(rf"\b(?:dm|message|contact|whatsapp)\b(?:\s+me)?(?:\s+[a-z0-9']+){{0,4}}\s+\b(?:for\s+)?{_DRUG_TERM}\b"), "contact-for-drugs"),
    (re.compile(rf"\b{_DRUG_TERM}\b(?:\s+[a-z0-9']+){{0,4}}\s+\b(?:dm|message|contact|whatsapp)\b"), "drugs-contact"),
    (re.compile(rf"\b(?:buy|order|get)\b(?:\s+[a-z0-9']+){{0,4}}\s+\b{_DRUG_TERM}\b"), "purchase-solicitation"),
)


_PORN_TERM = r"(?:porn(?:o|ography|ographic)?|xxx)"
_PORN_SOLICITATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"\b(?:dm|message|contact|whatsapp)\b(?:\s+me)?(?:\s+[a-z0-9']+){{0,4}}\s+\b(?:for\s+)?{_PORN_TERM}\b(?:\s+(?:video|videos|image|images|pics?|content))?"), "contact-for-pornography"),
    (re.compile(rf"\b{_PORN_TERM}\b(?:\s+(?:video|videos|image|images|pics?|content))?(?:\s+[a-z0-9']+){{0,3}}\s+\b(?:for\s+sale|available|in\s+stock)\b"), "pornography-sale"),
    (re.compile(rf"\b(?:i|we)\s+(?:have|sell|offer|share|send|provide)\b(?:\s+[a-z0-9']+){{0,5}}\s+\b{_PORN_TERM}\b(?:\s+(?:video|videos|image|images|pics?|content))?"), "pornography-offer"),
)


def _detect_contextual_pornography(normalized: str, *, field: str) -> dict[str, Any] | None:
    labels: list[str] = []
    for pattern, label in _PORN_SOLICITATION_PATTERNS:
        if pattern.search(normalized):
            labels.append(label)
    if not labels:
        return None
    return {
        "category": "pornography",
        "severity": "critical",
        "confidence": 0.97,
        "source": "text",
        "field": field,
        "detector": "aos_text_rules",
        "detector_version": "5",
        "reason": "pornographic solicitation or distribution",
        "evidence": sorted(set(labels))[:5],
    }

_NEGATION_SUFFIXES = ("do not ", "don't ", "never ", "avoid ", "stop ")


def _is_negated(normalized: str, start: int) -> bool:
    prefix = normalized[max(0, start - 24):start]
    return any(prefix.endswith(value) for value in _NEGATION_SUFFIXES)


def _detect_contextual_drug_sale(normalized: str, *, field: str) -> dict[str, Any] | None:
    labels: list[str] = []
    for pattern, label in _DRUG_SOLICITATION_PATTERNS:
        for match in pattern.finditer(normalized):
            if label == "purchase-solicitation" and _is_negated(normalized, match.start()):
                continue
            labels.append(label)
            break
    if not labels:
        return None
    return {
        "category": "drugs",
        "severity": "high",
        "confidence": 0.97,
        "source": "text",
        "field": field,
        "detector": "aos_text_rules",
        "detector_version": "5",
        "reason": "controlled-drug transaction or solicitation",
        "evidence": sorted(set(labels))[:5],
    }

_RULES: tuple[tuple[str, str, str, float, tuple[str, ...]], ...] = (
    ("pornography", "critical", "pornographic solicitation", 0.97, ("hardcore porn", "child porn", "porn video")),
    ("sexual_explicit", "high", "explicit sexual solicitation", 0.94, ("buy sex", "sexual services", "explicit sex video")),
    ("hate", "critical", "explicit hate advocacy", 0.97, ("kill all muslims", "kill all jews", "kill all christians", "kill all gay people", "kill all black people", "kill all white people", "exterminate all muslims", "exterminate all jews")),
    ("threats", "critical", "credible violent threat phrase", 0.94, ("i will kill you", "i'm going to kill you", "i will shoot you")),
    ("self_harm", "high", "self-harm intent phrase", 0.93, ("i will kill myself", "how to kill myself")),
    ("illegal_goods", "high", "illegal goods sale", 0.95, ("fake passport for sale", "fake id for sale")),
    ("weapons", "high", "weapon sale", 0.95, ("gun for sale", "pistol for sale", "rifle for sale", "ammo for sale")),
    ("drugs", "high", "hard-drug sale", 0.94, ("cocaine for sale", "heroin for sale", "meth for sale")),
    ("scam", "high", "advance-fee scam pattern", 0.93, ("pay a processing fee to release", "send money to unlock your prize")),
    ("fraud", "high", "credential theft pattern", 0.95, ("send me your otp", "share your verification code")),
    ("harassment", "high", "targeted degrading harassment", 0.94, ("you are worthless and should die", "nobody wants you alive")),
    ("spam", "medium", "repetitive promotion", 0.72, ("dm me now", "click link in bio")),
)


def _normalize(value: str) -> tuple[str, set[str]]:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().translate(_LEET)
    text = text.replace("’", "'").replace("`", "'")
    # Join punctuation-separated single-letter evasion (f.u.c.k / f u c k) while
    # leaving normal multi-word text intact.
    for match in list(_SINGLE_LETTER_RUN.finditer(text)):
        joined = re.sub(r"[^a-z]", "", match.group(0))
        text = text.replace(match.group(0), joined)
    tokens = set(_WORD.findall(text))
    normalized = " ".join(_WORD.findall(text))
    return normalized, tokens


def _phrase_matches(normalized: str, tokens: set[str], phrase: str) -> bool:
    parts = phrase.split()
    if len(parts) == 1:
        return parts[0] in tokens
    return re.search(r"(?:^|\s)" + r"\s+".join(re.escape(part) for part in parts) + r"(?:$|\s)", normalized) is not None


def detect_text(items: list[dict[str, Any]], *, max_chars: int) -> tuple[list[dict[str, Any]], list[str]]:
    signals: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in items:
        raw = str(item.get("text") or "")[:max_chars]
        if not raw.strip():
            continue
        field = str(item.get("field") or "text")[:80]
        normalized, tokens = _normalize(raw)
        if not normalized:
            continue
        contextual_drug_signal = _detect_contextual_drug_sale(normalized, field=field)
        if contextual_drug_signal:
            signals.append(contextual_drug_signal)
        contextual_porn_signal = _detect_contextual_pornography(normalized, field=field)
        if contextual_porn_signal:
            signals.append(contextual_porn_signal)

        for category, severity, rationale, confidence, families in _TOKEN_FAMILY_RULES:
            matched_labels = sorted({
                label
                for token in tokens
                for pattern, label in families
                if pattern.fullmatch(token)
            })
            if matched_labels:
                signals.append(
                    {
                        "category": category,
                        "severity": severity,
                        "confidence": confidence,
                        "source": "text",
                        "field": field,
                        "detector": "aos_text_rules",
                        "detector_version": "5",
                        "reason": rationale,
                        # Store canonical family labels, never the user's raw token/text.
                        "evidence": matched_labels[:5],
                    }
                )

        for category, severity, rationale, confidence, phrases in _RULES:
            matched = [phrase for phrase in phrases if _phrase_matches(normalized, tokens, phrase)]
            if not matched:
                continue
            signals.append(
                {
                    "category": category,
                    "severity": severity,
                    "confidence": confidence,
                    "source": "text",
                    "field": field,
                    "detector": "aos_text_rules",
                    "detector_version": "5",
                    "reason": rationale,
                    # Store only matched canonical rule labels, never full user text.
                    "evidence": matched[:5],
                }
            )
    return signals, failures
