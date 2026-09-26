from __future__ import annotations

import numpy as np

from app.runtime import TextSafetyRuntime


def test_binary_entailment_uses_contradiction_vs_entailment_only():
    logits = np.asarray([[0.0, 100.0, 2.0], [2.0, -100.0, 0.0]], dtype=np.float32)
    scores = TextSafetyRuntime._binary_entailment(logits, 2, 0)
    assert scores[0] > 0.87
    assert scores[1] < 0.13
