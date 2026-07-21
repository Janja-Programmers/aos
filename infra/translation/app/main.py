from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .languages import supported_languages
from .observability import instrument_app, readiness_error
from .translator import TranslationError, ValidationError, get_config, get_runtime

app = FastAPI(
	title="AOS Translation Service",
	version="1.0.0",
)
instrument_app(app, "aos-translation")
LOGGER = logging.getLogger(__name__)


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
		return readiness_error("translation_model", exc)


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

	except ValidationError:
		raise HTTPException(status_code=400, detail="Invalid translation request.")

	except TranslationError:
		raise HTTPException(status_code=500, detail="Translation provider failed.")

	except Exception:
		LOGGER.exception("Translation request failed")
		raise HTTPException(status_code=500, detail="Translation failed.")
