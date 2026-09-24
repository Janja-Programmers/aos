from __future__ import annotations

import hmac
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .languages import supported_languages
from .observability import instrument_app, readiness_error
from .translator import BusyError, TranslationError, ValidationError, get_config, get_runtime

app = FastAPI(title="AOS Translation Service", version="2.0.0")
instrument_app(app, "aos-translation")
LOGGER = logging.getLogger(__name__)


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(..., min_length=1, max_length=5000)
    target_language: str = Field(..., min_length=1, max_length=32)
    source_language: str | None = Field(default=None, max_length=32)


class TranslateResponse(BaseModel):
    source_language: str
    target_language: str
    source_language_label: str
    target_language_label: str
    translated_content: str
    provider: str
    model_name: str


def _internal_auth(x_aos_internal_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("TRANSLATION_INTERNAL_TOKEN", "").strip()
    if not expected:
        # A missing shared secret must never silently turn a private model service
        # into an unauthenticated API.
        raise HTTPException(status_code=503, detail="Service authentication is not configured.")
    if not hmac.compare_digest(expected, str(x_aos_internal_token or "")):
        raise HTTPException(status_code=401, detail="Authentication required.")


@app.get("/health")
def health():
    config = get_config()
    return {"ok": True, "service": "aos-translation", "model_name": config.model_name}


@app.get("/ready")
def ready():
    # Readiness stays unauthenticated so Docker/orchestrators can probe the
    # loopback/private service without putting the shared secret in process args.
    config = get_config()
    runtime = get_runtime()
    try:
        runtime.load()
        return {"ok": True, "service": "aos-translation", "model_name": config.model_name, "model_loaded": runtime.is_loaded}
    except Exception as exc:
        return readiness_error("translation_model", exc)


@app.get("/languages")
def languages(_: None = Depends(_internal_auth)):
    return {"items": supported_languages()}


@app.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest, _: None = Depends(_internal_auth)):
    try:
        result = get_runtime().translate(
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
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid translation request.")
    except BusyError:
        raise HTTPException(status_code=503, detail="Translation service is busy.")
    except TranslationError:
        raise HTTPException(status_code=500, detail="Translation provider failed.")
    except Exception:
        LOGGER.exception("Translation request failed")
        raise HTTPException(status_code=500, detail="Translation failed.")
