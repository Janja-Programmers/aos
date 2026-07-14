# AOS Translation Service

## Purpose

AOS uses a private translation service for chat message translation. The service owns the AI runtime, while the Frappe backend owns chat permissions, message access checks, rate limits, cache records, and API response serialization.

The production boundary is:

```text
Flutter / API client
→ Frappe chat translate endpoint
→ aos.integrations.ai.translation_client
→ private aos-translation service
→ NLLB runtime
→ Frappe saves/returns AOS Message Translation
```

The frontend should never call the translation container directly.

## Ownership boundary

### Frappe backend owns

- User authentication and session validation
- Chat conversation membership checks
- Message lookup and deleted-message rules
- Rate limiting
- Source/target language request validation
- Translation cache lookup and creation
- `AOS Message Translation` records
- Friendly business/API errors

### Translation service owns

- NLLB/CT2 model loading
- Tokenizer/runtime dependencies
- Device and compute-type configuration
- Language normalization
- Translation inference
- Provider/model metadata returned for audit/debugging

Do not import translation AI dependencies inside the Frappe backend. The backend should call the private HTTP service through `aos.integrations.ai.translation_client` only.

## Runtime service

Default private service URL:

```text
http://127.0.0.1:8100
```

Container/service name:

```text
aos-translation
```

Main endpoints:

```text
GET  /health
GET  /ready
GET  /languages
POST /translate
```

## Environment variables

Translation model/runtime variables belong in the infra `.env` file used by Docker Compose:

```env
TRANSLATION_BIND_ADDRESS=127.0.0.1
TRANSLATION_PORT=8100

TRANSLATION_MODEL_NAME=nllb-200-distilled-1.3B-ct2-int8
TRANSLATION_DEVICE=cpu
TRANSLATION_COMPUTE_TYPE=int8
TRANSLATION_DEFAULT_SOURCE_LANGUAGE=eng_Latn
TRANSLATION_MAX_CHARS=1000
TRANSLATION_MODEL_HOST_PATH=./models/nllb-200-distilled-1.3B-ct2-int8

TRANSLATION_MEMORY_LIMIT=4g
TRANSLATION_CPU_LIMIT=3.00
TRANSLATION_PIDS_LIMIT=1024
```

These runtime details should not be hard-coded in Frappe business logic.

## AOS Settings and environment

Configure the private translation service URL in `.env` and keep only product limits/timeouts in AOS Settings:

```text
# .env
TRANSLATION_SERVICE_URL=http://127.0.0.1:8100

# AOS Settings
translation_max_characters: 1000
translation_service_timeout_seconds: 30
```

Do not add provider/model/device/compute settings to Frappe unless they are passive display metadata. Model and runtime decisions belong to the translation service.

## Model setup

The default model path is:

```text
models/nllb-200-distilled-1.3B-ct2-int8
```

Example setup:

```bash
cd /home/aos/aos
mkdir -p models
git lfs install
git clone https://huggingface.co/OpenNMT/nllb-200-distilled-1.3B-ct2-int8 models/nllb-200-distilled-1.3B-ct2-int8
```

Confirm `.env` points to the model directory:

```env
TRANSLATION_MODEL_HOST_PATH=./models/nllb-200-distilled-1.3B-ct2-int8
```

## Deployment

Build and start only translation:

```bash
cd /home/aos/aos
docker compose build translation
docker compose up -d translation
```

Or start all infra services:

```bash
docker compose up -d --build
```

Check container status:

```bash
docker compose ps translation
```

## Health checks

```bash
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/ready
```

Expected readiness includes:

```json
{
  "ok": true,
  "model_loaded": true
}
```

## Direct service test

```bash
curl -X POST http://127.0.0.1:8100/translate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, how are you?",
    "source_language": "eng_Latn",
    "target_language": "swh_Latn"
  }'
```

Expected response includes translated content, normalized languages, and passive metadata such as provider/model name.

## Frappe client test

```bash
cd /home/aos/frappe-bench
bench --site <site> console
```

```python
from aos.integrations.ai.translation_client import health_check, ready_check, list_languages, translate_text

print(health_check())
print(ready_check())
print(list_languages())
print(
    translate_text(
        text="Hello, how are you?",
        source_language="eng_Latn",
        target_language="swh_Latn",
    )
)
```

## Frappe endpoint test

Use a message that belongs to a conversation the logged-in user can access:

```bash
curl -X POST "https://<site>/api/method/aos.api.v1.chat.translate_message" \
  -H "Cookie: sid=YOUR_SID" \
  -F "message_id=MSG-YYYY-XXXXX" \
  -F "target_language=swh_Latn"
```

Expected success shape:

```json
{
  "message": {
    "ok": true,
    "message": "Message translated.",
    "data": {
      "message_id": "MSG-YYYY-XXXXX",
      "source_language": "eng_Latn",
      "target_language": "swh_Latn",
      "translated_content": "...",
      "cached": false
    }
  }
}
```

Call the same request twice. The second response should return the saved `AOS Message Translation` record with:

```json
{
  "cached": true
}
```

## Cache behavior

`AOS Message Translation` caches translations by:

```text
message + target_language + original_content_hash
```

The content hash prevents stale translations when the original message changes. Provider/model metadata may be saved for audit/debugging, but should not control business logic.

## Failure behavior

If the translation service is unavailable, the Frappe endpoint should return a friendly temporary error instead of exposing infrastructure details.

Recommended code:

```text
TRANSLATION_UNAVAILABLE
```

To test service-down behavior:

```bash
cd /home/aos/aos
docker compose stop translation
```

Then call the Frappe translation endpoint. Restore the service afterward:

```bash
docker compose up -d translation
```

## Operational notes

- Keep `translation` bound to `127.0.0.1`.
- Do not expose port `8100` publicly.
- Monitor memory during model loading.
- Use `docker compose logs --tail=200 translation` for runtime failures.
- Verify model files exist before blaming Frappe.
- If direct service tests pass but Frappe fails, inspect AOS Settings, chat permissions, rate limits, and Frappe logs.
