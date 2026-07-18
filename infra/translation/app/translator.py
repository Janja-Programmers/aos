from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from .languages import LanguageInfo, normalize_language_code


@dataclass(frozen=True)
class TranslationConfig:
    model_path: str
    model_name: str
    device: str
    compute_type: str
    default_source_language: str
    max_chars: int


@dataclass(frozen=True)
class TranslationResult:
    source_language: str
    target_language: str
    source_language_label: str
    target_language_label: str
    translated_content: str
    provider: str
    model_name: str


class TranslationError(Exception):
    pass


class ValidationError(TranslationError):
    pass


class TranslatorRuntime:
    def __init__(self, config: TranslationConfig):
        self.config = config
        self._translator: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            # Keep health/config imports model-free; runtime compatibility CI
            # separately installs and imports the full production dependency set.
            import ctranslate2
            from transformers import AutoTokenizer

            if self._translator is None:
                if not os.path.isdir(self.config.model_path):
                    raise TranslationError(
                        f"Translation model path does not exist: {self.config.model_path}"
                    )

                self._translator = ctranslate2.Translator(
                    self.config.model_path,
                    device=self.config.device,
                    compute_type=self.config.compute_type,
                )

            if self._tokenizer is None:
                self._tokenizer = AutoTokenizer.from_pretrained(
                    self.config.model_path,
                    local_files_only=True,
                )

    @property
    def is_loaded(self) -> bool:
        return self._translator is not None and self._tokenizer is not None

    def translate(
        self,
        *,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> TranslationResult:
        clean_text = self._clean_text(text)

        target = normalize_language_code(target_language)
        if target is None:
            raise ValidationError("Unsupported target language.")

        source = normalize_language_code(source_language) if source_language else None
        if source is None:
            source = normalize_language_code(self.config.default_source_language)

        if source is None:
            raise ValidationError("Unsupported source language.")

        if source.code == target.code:
            return TranslationResult(
                source_language=source.code,
                target_language=target.code,
                source_language_label=source.label,
                target_language_label=target.label,
                translated_content=clean_text,
                provider="nllb",
                model_name=self.config.model_name,
            )

        self.load()

        assert self._translator is not None
        assert self._tokenizer is not None

        tokenizer = self._tokenizer
        tokenizer.src_lang = source.code

        input_ids = tokenizer.encode(clean_text)
        source_tokens = tokenizer.convert_ids_to_tokens(input_ids)

        result = self._translator.translate_batch(
            [source_tokens],
            target_prefix=[[target.code]],
            beam_size=4,
            max_decoding_length=256,
            repetition_penalty=1.1,
        )

        output_tokens = result[0].hypotheses[0]

        # Remove forced target language token.
        if output_tokens and output_tokens[0] == target.code:
            output_tokens = output_tokens[1:]

        output_ids = tokenizer.convert_tokens_to_ids(output_tokens)
        translated = tokenizer.decode(
            output_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        ).strip()

        if not translated:
            raise TranslationError("Translation output was empty.")

        return TranslationResult(
            source_language=source.code,
            target_language=target.code,
            source_language_label=source.label,
            target_language_label=target.label,
            translated_content=translated,
            provider="nllb",
            model_name=self.config.model_name,
        )

    def _clean_text(self, text: str) -> str:
        clean = (text or "").strip()

        if not clean:
            raise ValidationError("Text is required.")

        if len(clean) > self.config.max_chars:
            raise ValidationError(
                f"Text cannot exceed {self.config.max_chars} characters."
            )

        return clean


_runtime: TranslatorRuntime | None = None


def get_config() -> TranslationConfig:
    return TranslationConfig(
        model_path=os.getenv("TRANSLATION_MODEL_PATH", "/models/nllb"),
        model_name=os.getenv(
            "TRANSLATION_MODEL_NAME",
            "nllb-200-distilled-1.3B-ct2-int8",
        ),
        device=os.getenv("TRANSLATION_DEVICE", "cpu"),
        compute_type=os.getenv("TRANSLATION_COMPUTE_TYPE", "int8"),
        default_source_language=os.getenv(
            "TRANSLATION_DEFAULT_SOURCE_LANGUAGE",
            "eng_Latn",
        ),
        max_chars=int(os.getenv("TRANSLATION_MAX_CHARS", "1000")),
    )


def get_runtime() -> TranslatorRuntime:
    global _runtime

    if _runtime is None:
        _runtime = TranslatorRuntime(get_config())

    return _runtime
