from __future__ import annotations

import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .languages import supported_languages
from .translator import TranslationError, ValidationError, get_config, get_runtime


app = FastAPI(
    title="AOS Translation Service",
    version="1.0.0",
)


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    target_language: str = Field(..., min_length=1)
    source_language: str | None = None


class TranslateResponse(BaseModel):
    source_language: str
    target_language: str
    source_language_label: str
    target_language_label: str
    translated_content: str
    provider: str
    model_name: str


@app.get("/health")
def health():
    config = get_config()

    return {
        "ok": True,
        "service": "aos-translation",
        "model_name": config.model_name,
        "device": config.device,
        "compute_type": config.compute_type,
    }


@app.get("/ready")
def ready():
    config = get_config()
    runtime = get_runtime()

    try:
        runtime.load()

        return {
            "ok": True,
            "service": "aos-translation",
            "model_name": config.model_name,
            "device": config.device,
            "compute_type": config.compute_type,
            "model_loaded": runtime.is_loaded,
        }

    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/languages")
def languages():
    return {
        "items": supported_languages(),
    }


@app.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest):
    runtime = get_runtime()

    try:
        result = runtime.translate(
            text=payload.text,
            source_language=payload.source_language,
            target_language=payload.target_language,
        )

        return {
            "source_language": result.source_language,
            "target_language": result.target_language,
            "source_language_label": result.source_language_label,
            "target_language_label": result.target_language_label,
            "translated_content": result.translated_content,
            "provider": result.provider,
            "model_name": result.model_name,
        }

    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except TranslationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Translation failed: {exc}")
