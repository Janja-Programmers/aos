from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    label: str


LANGUAGE_CODE_MAP: dict[str, LanguageInfo] = {
    # English
    "en": LanguageInfo("eng_Latn", "English"),
    "eng": LanguageInfo("eng_Latn", "English"),
    "eng_latn": LanguageInfo("eng_Latn", "English"),

    # Swahili
    "sw": LanguageInfo("swh_Latn", "Swahili"),
    "swh": LanguageInfo("swh_Latn", "Swahili"),
    "swh_latn": LanguageInfo("swh_Latn", "Swahili"),

    # French
    "fr": LanguageInfo("fra_Latn", "French"),
    "fra": LanguageInfo("fra_Latn", "French"),
    "fra_latn": LanguageInfo("fra_Latn", "French"),

    # Spanish
    "es": LanguageInfo("spa_Latn", "Spanish"),
    "spa": LanguageInfo("spa_Latn", "Spanish"),
    "spa_latn": LanguageInfo("spa_Latn", "Spanish"),

    # Arabic
    "ar": LanguageInfo("arb_Arab", "Arabic"),
    "arb": LanguageInfo("arb_Arab", "Arabic"),
    "arb_arab": LanguageInfo("arb_Arab", "Arabic"),

    # Chinese simplified / traditional
    "zh": LanguageInfo("zho_Hans", "Chinese"),
    "zh-cn": LanguageInfo("zho_Hans", "Chinese Simplified"),
    "zh_hans": LanguageInfo("zho_Hans", "Chinese Simplified"),
    "zho_hans": LanguageInfo("zho_Hans", "Chinese Simplified"),
    "zh-tw": LanguageInfo("zho_Hant", "Chinese Traditional"),
    "zh_hant": LanguageInfo("zho_Hant", "Chinese Traditional"),
    "zho_hant": LanguageInfo("zho_Hant", "Chinese Traditional"),

    # Portuguese
    "pt": LanguageInfo("por_Latn", "Portuguese"),
    "por": LanguageInfo("por_Latn", "Portuguese"),
    "por_latn": LanguageInfo("por_Latn", "Portuguese"),

    # German
    "de": LanguageInfo("deu_Latn", "German"),
    "deu": LanguageInfo("deu_Latn", "German"),
    "deu_latn": LanguageInfo("deu_Latn", "German"),

    # Italian
    "it": LanguageInfo("ita_Latn", "Italian"),
    "ita": LanguageInfo("ita_Latn", "Italian"),
    "ita_latn": LanguageInfo("ita_Latn", "Italian"),

    # Hindi
    "hi": LanguageInfo("hin_Deva", "Hindi"),
    "hin": LanguageInfo("hin_Deva", "Hindi"),
    "hin_deva": LanguageInfo("hin_Deva", "Hindi"),

    # Somali
    "so": LanguageInfo("som_Latn", "Somali"),
    "som": LanguageInfo("som_Latn", "Somali"),
    "som_latn": LanguageInfo("som_Latn", "Somali"),

    # Amharic
    "am": LanguageInfo("amh_Ethi", "Amharic"),
    "amh": LanguageInfo("amh_Ethi", "Amharic"),
    "amh_ethi": LanguageInfo("amh_Ethi", "Amharic"),

    # Yoruba
    "yo": LanguageInfo("yor_Latn", "Yoruba"),
    "yor": LanguageInfo("yor_Latn", "Yoruba"),
    "yor_latn": LanguageInfo("yor_Latn", "Yoruba"),

    # Hausa
    "ha": LanguageInfo("hau_Latn", "Hausa"),
    "hau": LanguageInfo("hau_Latn", "Hausa"),
    "hau_latn": LanguageInfo("hau_Latn", "Hausa"),

    # Zulu
    "zu": LanguageInfo("zul_Latn", "Zulu"),
    "zul": LanguageInfo("zul_Latn", "Zulu"),
    "zul_latn": LanguageInfo("zul_Latn", "Zulu"),
}


def normalize_language_code(value: str | None) -> LanguageInfo | None:
    if not value:
        return None

    raw = str(value).strip()

    if not raw:
        return None

    # Already normalized NLLB code.
    if "_" in raw and len(raw) >= 7:
        key = raw.lower()
        return LANGUAGE_CODE_MAP.get(key) or LanguageInfo(raw, raw)

    key = raw.lower().replace("_", "-")
    return LANGUAGE_CODE_MAP.get(key)


def supported_languages() -> list[dict[str, str]]:
    seen: set[str] = set()
    items: list[dict[str, str]] = []

    for info in LANGUAGE_CODE_MAP.values():
        if info.code in seen:
            continue

        seen.add(info.code)
        items.append(
            {
                "code": info.code,
                "label": info.label,
            }
        )

    return sorted(items, key=lambda item: item["label"])
