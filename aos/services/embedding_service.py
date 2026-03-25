"""
Embedding Service (OpenCLIP).

Handles:
- Model loading (singleton)
- Image preprocessing
- Embedding generation
"""

from __future__ import annotations

from typing import List

import torch
import open_clip
from PIL import Image

# Good balance of speed vs quality
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

# Use GPU if available
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# MODEL SINGLETO
_model = None
_preprocess = None


def _load_model():
    global _model, _preprocess

    if _model is None:
        model, _, preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME,
            pretrained=PRETRAINED,
        )

        model = model.to(DEVICE)
        model.eval()

        _model = model
        _preprocess = preprocess

    return _model, _preprocess

# EMBEDDING
def generate_image_embedding(image: Image.Image) -> List[float]:
    """
    Generate embedding vector from PIL Image.

    Returns:
        List[float] → normalized embedding
    """

    model, preprocess = _load_model()

    try:
        # Preprocess image
        image_input = preprocess(image).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            image_features = model.encode_image(image_input)

            # Normalize (VERY IMPORTANT for cosine similarity)
            image_features /= image_features.norm(dim=-1, keepdim=True)

        # Convert to Python list
        vector = image_features.squeeze(0).cpu().tolist()

        return vector

    except Exception:
        return []

def generate_text_embedding(text: str) -> List[float]:
    """
    Optional: enables hybrid search (image + text).
    """

    model, _ = _load_model()

    try:
        tokenizer = open_clip.get_tokenizer(MODEL_NAME)

        text_tokens = tokenizer([text]).to(DEVICE)

        with torch.no_grad():
            text_features = model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)

        return text_features.squeeze(0).cpu().tolist()

    except Exception:
        return []
