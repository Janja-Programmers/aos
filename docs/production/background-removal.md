# AOS Background Removal Operations

Background removal is a core AI media feature and is expected to be available in production. It runs outside the Frappe business backend so `rembg`, ONNX Runtime, and model dependencies do not affect Frappe workers.

## Architecture

```text
AOS Frappe backend
  -> private background-removal HTTP service
      -> rembg / ONNX Runtime
      -> transparent PNG output
```

Ownership boundary:

- Frappe owns authentication, file ownership checks, rate limits, Frappe File records, and API response formatting.
- Background removal owns image processing, model runtime, output PNG normalization, and processor/model configuration.
- The frontend must call the Frappe endpoint, not the private background-removal service directly.

The Frappe backend must not import `rembg`, `onnxruntime`, `numba`, `llvmlite`, or background-removal model logic.

## Services

Start the service:

```bash
docker compose up -d background-removal
```

Check status:

```bash
docker compose ps background-removal
curl http://127.0.0.1:8120/health
curl http://127.0.0.1:8120/ready
```

`/health` should be fast. `/ready` verifies that the processor can be initialized, so it may be slower during cold startup.

## AOS Settings

Configure only the background-removal service connection in AOS Settings:

```text
background_removal_service_url: http://127.0.0.1:8120
background_removal_service_timeout_seconds: 30
background_removal_max_image_bytes: 10485760
```

For a host-based Frappe deployment with Docker Compose services bound privately, use:

```text
http://127.0.0.1:8120
```

If Frappe also runs inside the Docker network, use:

```text
http://aos-background-removal:8000
```

Do not configure model/runtime details in AOS Settings. Model name, device, output format, memory limits, and other runtime values belong to the background-removal service environment.

## API behavior

Frontend flow:

```text
Flutter image editor
  -> Frappe remove_background endpoint
      -> private background-removal service
      -> new transparent PNG saved as Frappe File
      -> file_url returned to Flutter
```

The original uploaded image remains unchanged. The processed result is stored as a new PNG file.

Expected success response from Frappe:

```json
{
  "ok": true,
  "message": "Background removed successfully.",
  "data": {
    "file_id": "...",
    "file_url": "/files/example_no_bg_ab12cd34.png",
    "file_name": "example_no_bg_ab12cd34.png",
    "is_private": 0,
    "content_type": "image/png",
    "width": 1080,
    "height": 1080
  }
}
```

## Failure behavior

Background removal is always expected to be available. Do not add a product-level disable flag.

Expected behavior:

- File ownership, file type, size, and dimension validation happen in Frappe.
- Model processing happens only in the private service.
- If the service is down, Frappe returns a friendly temporary unavailable error.
- Internal model, runtime, and network details should be logged server-side, not exposed to clients.

Expected unavailable response:

```json
{
  "ok": false,
  "message": "Background removal is temporarily unavailable. Please try again later.",
  "code": "BACKGROUND_REMOVAL_UNAVAILABLE"
}
```

## Direct service test

```bash
curl -X POST http://127.0.0.1:8120/remove-background \
  -F "image=@/path/to/test-image.jpg" \
  --output removed-bg.png

file removed-bg.png
```

Expected:

```text
PNG image data
```

## Recovery

Background removal has no persistent vector database or generated index. Recovery is normally:

```bash
cd /home/aos/aos
docker compose up -d --build background-removal
curl http://127.0.0.1:8120/ready
```

If the service cannot initialize, inspect:

```bash
docker compose logs --tail=200 background-removal
docker stats --no-stream
```

Common causes are insufficient memory, failed model download/cache, invalid image input, or network restrictions during first model initialization.
