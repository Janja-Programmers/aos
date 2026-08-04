# AOS Video Processing Service

Phase 1 separates heavy Shorts video processing from Frappe.

## Ownership boundary

Frappe owns business logic:

- user permissions
- `AOS Short`
- `AOS Media Object`
- `AOS Video Processing Job`
- job dispatch and callback validation
- final status transitions

The video service owns heavy execution:

- raw video download from MinIO
- ffprobe duration probing
- ffmpeg final MP4 generation
- HLS generation
- thumbnail extraction
- representative-frame extraction and signed OpenCLIP classification request
- output upload to MinIO
- callback to Frappe

## Runtime flow

1. Flutter uploads `short_video_raw` using `aos.api.v1.media.init_upload` and `confirm_upload`.
2. Flutter calls `aos.api.v1.shorts.create_short` with `raw_video_media`.
3. Frappe creates `AOS Short` and `AOS Video Processing Job`.
4. Frappe enqueues `aos.tasks.video_processing.dispatch_video_processing_job` to Frappe Redis Queue.
5. The dispatcher posts the job to `aos-video-api`.
6. `aos-video-api` enqueues the FFmpeg job into the video service Redis/RQ queue.
7. `aos-video-worker` processes the video and uploads outputs to MinIO.
8. `aos-video-worker` calls `aos.api.v1.video_processing.handle_callback`.
9. Frappe stores validated visual classification evidence, updates `AOS Short`, creates thumbnail media metadata, and marks the job ready/failed.
10. Metadata publication fuses visual evidence with caption/hashtags and trusted ad context.

## Docker services

- `video-redis`: Redis instance dedicated to the video service queue.
- `video-api`: private FastAPI service, exposed only on host localhost.
- `video-worker`: RQ worker with FFmpeg installed.

## Required env

```env
VIDEO_SERVICE_URL=http://127.0.0.1:8130
VIDEO_SERVICE_SECRET=change-this-long-random-video-dispatch-secret
VIDEO_SERVICE_CALLBACK_SECRET=change-this-long-random-video-callback-secret
VIDEO_CALLBACK_URL=https://api.example.com/api/method/aos.api.v1.video_processing.handle_callback
VIDEO_REDIS_URL=redis://video-redis:6379/0
VIDEO_QUEUE_NAME=video
SHORT_CLASSIFICATION_SECRET=change-this-long-random-short-classification-secret
VIDEO_CLASSIFICATION_URL=http://image-search:8000/internal/shorts/classify-frames
VIDEO_CLASSIFICATION_ALLOWED_HOSTS=image-search
VIDEO_MINIO_ENDPOINT=minio:9000
```

## Security

Frappe signs dispatch requests with `VIDEO_SERVICE_SECRET` using HMAC-SHA256 in
`X-AOS-Signature`.

The video worker signs callbacks with `VIDEO_SERVICE_CALLBACK_SECRET` using
HMAC-SHA256 in `X-AOS-Callback-Signature`.

The video worker signs `X-AOS-Timestamp + "." + request_body` with `SHORT_CLASSIFICATION_SECRET` in `X-AOS-Signature`. The image service rejects requests more than five minutes old. Classification failure is non-fatal and never prevents a playable Short.

## Notes

The video service does not know AOS business rules. It processes only the object
keys and options Frappe sends. Frappe remains the source of truth.
