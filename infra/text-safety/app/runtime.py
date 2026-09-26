from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings, get_settings


class TextSafetyError(RuntimeError):
    pass


class TextSafetyBusyError(TextSafetyError):
    pass


@dataclass(frozen=True)
class CategorySpec:
    category: str
    severity: str
    hypotheses: tuple[str, ...]


# Hypotheses intentionally describe policy-violating meaning, not mere topic
# mentions. This reduces false positives for educational/news/reporting context.
CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec("sexual_explicit", "high", ("This text contains explicit sexual language, sexual solicitation, or graphically sexual content.", "This text explicitly discusses sexual acts, genitals, or sexual services in a sexualized way.")),
    CategorySpec("pornography", "critical", ("This text requests, offers, promotes, distributes, or graphically describes pornographic material.", "This text offers or sells porn, porno, pornography, or explicit sexual videos or images.", "This text asks someone to contact or message the author for pornographic material.")),
    CategorySpec("nudity", "high", ("This text requests, promotes, or sexualizes nudity or nude imagery.",)),
    CategorySpec("weapons", "high", ("This text requests, offers, promotes, or facilitates weapons, firearms, ammunition, or weapon use.",)),
    CategorySpec("violence", "high", ("This text advocates, threatens, celebrates, or graphically describes physical violence against people.",)),
    CategorySpec("graphic_violence", "critical", ("This text contains or promotes graphic gore, mutilation, or extreme violent injury.",)),
    CategorySpec("profanity", "high", ("This text uses strong obscene or profane language directed at a person or as abusive expression.",)),
    CategorySpec("hate", "critical", ("This text attacks, dehumanizes, excludes, or advocates harm against people because of a protected identity.",)),
    CategorySpec("harassment", "high", ("This text targets a person with degrading, abusive, humiliating, or bullying language.",)),
    CategorySpec("threats", "critical", ("This text contains a threat or stated intent to harm, kill, attack, or injure someone.",)),
    CategorySpec("self_harm", "high", ("This text encourages, instructs, or expresses intent for suicide or self-harm.",)),
    CategorySpec("illegal_goods", "high", ("This text offers, requests, sells, or facilitates illegal goods, forged documents, or contraband.",)),
    CategorySpec("dangerous_content", "high", ("This text gives or promotes instructions for seriously dangerous harmful acts.",)),
    CategorySpec("drugs", "high", ("This text offers, requests, sells, supplies, or facilitates illegal or recreational drugs.", "This text asks someone to contact the author to buy or obtain illegal drugs.")),
    CategorySpec("spam", "medium", ("This text is unsolicited repetitive promotion, mass solicitation, or manipulative spam.",)),
    CategorySpec("scam", "high", ("This text attempts a scam, deceptive money solicitation, fake prize, or confidence trick.",)),
    CategorySpec("fraud", "high", ("This text facilitates fraud, credential theft, impersonation, payment theft, or account takeover.",)),
)


class TextSafetyRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._session: Any | None = None
        self._load_lock = threading.Lock()
        self._local = threading.local()
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_requests)
        self._entailment_index = 2
        self._contradiction_index = 0

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    def _config_indices(self, model_path: Path) -> tuple[int, int]:
        try:
            raw = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        except Exception:
            return 2, 0
        label2id = {str(k).lower(): int(v) for k, v in dict(raw.get("label2id") or {}).items() if str(v).isdigit() or isinstance(v, int)}
        entailment = next((value for key, value in label2id.items() if "entail" in key), None)
        contradiction = next((value for key, value in label2id.items() if "contrad" in key), None)
        if entailment is None:
            id2label = {int(k): str(v).lower() for k, v in dict(raw.get("id2label") or {}).items()}
            entailment = next((key for key, value in id2label.items() if "entail" in value), 2)
            contradiction = next((key for key, value in id2label.items() if "contrad" in value), 0)
        return int(entailment), int(contradiction if contradiction is not None else 0)

    def load(self) -> None:
        if self._session is not None:
            return
        with self._load_lock:
            if self._session is not None:
                return
            model_path = Path(self.settings.model_path)
            model_file = model_path / "onnx" / "model.onnx"
            if not model_file.is_file():
                raise TextSafetyError("Text safety model is unavailable.")
            try:
                import onnxruntime as ort
                from transformers import AutoTokenizer

                options = ort.SessionOptions()
                options.intra_op_num_threads = self.settings.intra_op_threads
                options.inter_op_num_threads = self.settings.inter_op_threads
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(model_file),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
                # Validate tokenizer assets during readiness. Each worker thread
                # gets its own tokenizer instance to avoid mutable shared state.
                AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, use_fast=True)
                self._entailment_index, self._contradiction_index = self._config_indices(model_path)
            except TextSafetyError:
                raise
            except Exception as exc:
                self._session = None
                raise TextSafetyError("Text safety model failed to load.") from exc

    def _tokenizer(self):
        tokenizer = getattr(self._local, "tokenizer", None)
        if tokenizer is None:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(self.settings.model_path, local_files_only=True, use_fast=True)
            self._local.tokenizer = tokenizer
        return tokenizer

    @staticmethod
    def _binary_entailment(logits: np.ndarray, entailment_index: int, contradiction_index: int) -> np.ndarray:
        pair = logits[:, [contradiction_index, entailment_index]].astype(np.float64)
        pair -= np.max(pair, axis=1, keepdims=True)
        exp = np.exp(pair)
        probs = exp / np.sum(exp, axis=1, keepdims=True)
        return probs[:, 1]

    def classify(self, items: list[dict[str, str]]) -> dict[str, Any]:
        if not self._slots.acquire(blocking=False):
            raise TextSafetyBusyError("Text safety service is busy.")
        try:
            self.load()
            tokenizer = self._tokenizer()
            session = self._session
            if session is None:
                raise TextSafetyError("Text safety model is unavailable.")
            input_names = {item.name for item in session.get_inputs()}
            output_name = session.get_outputs()[0].name
            signals: list[dict[str, Any]] = []
            for item in items[: self.settings.max_items]:
                field = str(item.get("field") or "text")[:80]
                text = str(item.get("text") or "").strip()[: self.settings.max_chars_per_item]
                if not text:
                    continue
                pairs: list[tuple[CategorySpec, str]] = [
                    (spec, hypothesis)
                    for spec in CATEGORY_SPECS
                    for hypothesis in spec.hypotheses
                ]
                hypotheses = [hypothesis for _spec, hypothesis in pairs]
                encoded = tokenizer(
                    [text] * len(hypotheses),
                    hypotheses,
                    padding=True,
                    truncation="only_first",
                    max_length=self.settings.max_tokens,
                    return_tensors="np",
                )
                feeds = {
                    key: np.asarray(value, dtype=np.int64)
                    for key, value in encoded.items()
                    if key in input_names
                }
                logits = np.asarray(session.run([output_name], feeds)[0])
                scores = self._binary_entailment(logits, self._entailment_index, self._contradiction_index)
                category_scores: dict[str, tuple[CategorySpec, float]] = {}
                for (spec, _hypothesis), raw_score in zip(pairs, scores, strict=True):
                    confidence = float(max(0.0, min(float(raw_score), 1.0)))
                    previous = category_scores.get(spec.category)
                    if previous is None or confidence > previous[1]:
                        category_scores[spec.category] = (spec, confidence)
                for spec, confidence in category_scores.values():
                    # This is only a bounded transport/audit floor. The canonical
                    # AOS policy owns review/reject thresholds.
                    if confidence < self.settings.evidence_floor:
                        continue
                    signals.append({
                        "field": field,
                        "category": spec.category,
                        "severity": spec.severity,
                        "confidence": round(confidence, 6),
                    })
            signals.sort(key=lambda row: float(row.get("confidence") or 0), reverse=True)
            return {
                "status": "ready",
                "signals": signals[:64],
                "model": self.settings.model_name,
                "model_version": self.settings.model_version,
            }
        finally:
            self._slots.release()


_runtime: TextSafetyRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> TextSafetyRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = TextSafetyRuntime(get_settings())
    return _runtime
