from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from app.runtime import CATEGORY_SPECS, TextSafetyRuntime


def test_binary_entailment_uses_contradiction_vs_entailment_only():
	logits = np.asarray([[0.0, 100.0, 2.0], [2.0, -100.0, 0.0]], dtype=np.float32)
	scores = TextSafetyRuntime._binary_entailment(logits, 2, 0)
	assert scores[0] > 0.87
	assert scores[1] < 0.13


def test_provider_keeps_moderate_evidence_for_central_policy():
	settings = SimpleNamespace(
		max_concurrent_requests=1,
		max_items=4,
		max_chars_per_item=6000,
		max_tokens=256,
		evidence_floor=0.05,
		model_name="semantic-test",
		model_version="2",
	)
	runtime = TextSafetyRuntime(settings)

	class Input:
		name = "input_ids"

	class Output:
		name = "logits"

	class Session:
		def get_inputs(self):
			return [Input()]

		def get_outputs(self):
			return [Output()]

		def run(self, _outputs, feeds):
			count = int(np.asarray(feeds["input_ids"]).shape[0])
			# contradiction=0.0, entailment about -0.405 => binary entailment ~= 0.40.
			logits = np.zeros((count, 3), dtype=np.float32)
			logits[:, 2] = -0.4054651
			return [logits]

	runtime._session = Session()
	runtime._tokenizer = lambda: (
		lambda _premises, hypotheses, **_kwargs: {"input_ids": np.ones((len(hypotheses), 4), dtype=np.int64)}
	)
	result = runtime.classify([{"field": "comment", "text": "example"}])
	assert result["signals"]
	assert all(0.39 <= float(row["confidence"]) <= 0.41 for row in result["signals"])
	assert {row["category"] for row in result["signals"]} == {spec.category for spec in CATEGORY_SPECS}


def test_semantic_empty_values_do_not_load_or_invoke_model(monkeypatch):
	settings = SimpleNamespace(
		max_concurrent_requests=1,
		max_items=8,
		max_chars_per_item=6000,
		max_tokens=256,
		evidence_floor=0.05,
		model_name="semantic-test",
		model_version="3",
	)
	runtime = TextSafetyRuntime(settings)
	monkeypatch.setattr(
		runtime,
		"load",
		lambda: (_ for _ in ()).throw(AssertionError("empty text must not load model")),
	)
	result = runtime.classify(
		[
			{"field": "a", "text": "[]"},
			{"field": "b", "text": "{}"},
			{"field": "c", "text": "null"},
			{"field": "d", "text": "   "},
			{"field": "e", "text": "!!!..."},
			{"field": "f", "text": '["", "   "]'},
		]
	)
	assert result["signals"] == []
	assert result["model_version"] == "3"


def test_runtime_flattens_serialized_string_arrays_before_inference():
	settings = SimpleNamespace(
		max_concurrent_requests=1,
		max_items=4,
		max_chars_per_item=6000,
		max_tokens=256,
		evidence_floor=0.05,
		model_name="semantic-test",
		model_version="3",
	)
	runtime = TextSafetyRuntime(settings)
	seen = []

	class Input:
		name = "input_ids"

	class Output:
		name = "logits"

	class Session:
		def get_inputs(self):
			return [Input()]

		def get_outputs(self):
			return [Output()]

		def run(self, _outputs, feeds):
			count = int(np.asarray(feeds["input_ids"]).shape[0])
			logits = np.zeros((count, 3), dtype=np.float32)
			logits[:, 2] = -20.0
			return [logits]

	runtime._session = Session()

	def tokenizer(premises, hypotheses, **_kwargs):
		seen.extend(premises)
		return {"input_ids": np.ones((len(hypotheses), 4), dtype=np.int64)}

	runtime._tokenizer = lambda: tokenizer
	runtime.classify([{"field": "hashtags", "text": '["travel", "beach", "travel"]'}])
	assert seen
	assert set(seen) == {"travel beach"}
