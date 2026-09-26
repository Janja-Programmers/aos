from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    destination = Path(os.getenv("TEXT_SAFETY_MODEL_PATH", "/models/text-safety"))
    repo_id = os.getenv("TEXT_SAFETY_MODEL_REPO", "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli")
    revision = os.getenv("TEXT_SAFETY_MODEL_REVISION", "acf08db83390e23428c560cb578a865b39196993")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=str(destination),
        token=os.getenv("HF_TOKEN") or None,
        allow_patterns=[
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
            "onnx/model.onnx",
        ],
    )
    required = [destination / "config.json", destination / "tokenizer.json", destination / "onnx" / "model.onnx"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Model download incomplete: {missing}")
    print(f"Pinned text-safety model downloaded to {destination}")


if __name__ == "__main__":
    main()
