# Automatic content classification

AOS persists exactly four Short content modes: `shop`, `geo`, `vibes`, and `learn`. `all` is only a feed aggregation and is never stored on `AOS Short`.

## Lifecycle

1. The authenticated video worker samples up to `VIDEO_CLASSIFICATION_FRAME_COUNT` representative frames across the video.
2. It sends the bounded JPEG frames to the private image-search service using an HMAC-signed, five-minute freshness-bound request.
3. OpenCLIP compares the frames with versioned prompt ensembles for Shop, Geo, Vibes, and Learn.
4. The signed video callback stores validated visual scores as non-authoritative evidence.
5. On `update_short_metadata`, Frappe fuses visual scores with normalized caption and hashtag signals.
6. A validated owned active `ad_id` is authoritative commerce context and assigns `shop`.
7. The final mode, confidence, source, model version, and classification time are stored before moderation is queued.

Creator-supplied `content_mode` is accepted only so old clients do not fail strict-field validation. It is ignored when choosing the final mode.

## Safety and fallback

Classification failure never fails video processing or publication. If visual and text evidence are unavailable, the Short falls back to `vibes`. A visual Shop prediction without a valid owned active ad is not permitted to bypass existing commerce rules; the best non-Shop mode is selected, or `vibes` is used as fallback.

The public API exposes the final `content_mode` and a bounded classification summary. Raw frame data, prompt text, and score maps remain internal. No public manual-classification endpoint is introduced; future controlled administrative correction or reclassification can preserve canonical Short IDs.

## Configuration

```env
SHORT_CLASSIFICATION_SECRET=<openssl-rand-hex-32>
VIDEO_CLASSIFICATION_ENABLED=true
VIDEO_CLASSIFICATION_URL=http://image-search:8000/internal/shorts/classify-frames
VIDEO_CLASSIFICATION_ALLOWED_HOSTS=image-search
VIDEO_CLASSIFICATION_TIMEOUT_SECONDS=45
VIDEO_CLASSIFICATION_FRAME_COUNT=5
VIDEO_CLASSIFICATION_MAX_FRAME_BYTES=1048576
IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_FRAMES=6
IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_FRAME_BYTES=1048576
IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_TOTAL_BYTES=6291456
IMAGE_SEARCH_SHORT_CLASSIFICATION_TEMPERATURE=0.05
IMAGE_SEARCH_SHORT_CLASSIFICATION_MODEL_VERSION=openclip-v1
```

The same `SHORT_CLASSIFICATION_SECRET` must be provided to `image-search`, `video-api`, and `video-worker` through Docker Compose. Rotate it as a coordinated deployment.


## Current model boundary

The production implementation uses representative visual frames plus caption and hashtag evidence. It does not yet transcribe spoken audio or OCR every frame. Videos with weak visual/text evidence therefore use the safe `vibes` fallback and remain visible in `all`; this is preferable to blocking publication or silently trusting a creator-selected mode.
