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

1. Flutter uploads `short_video_raw` using `aos.api.v1.media.init_upload` with `upload_mode=auto`; small files use direct PUT + `confirm_upload`, while large files use resumable multipart + `complete_multipart_upload`.
2. Flutter calls `aos.api.v1.shorts.create_short` with `raw_video_media`.
3. Frappe creates `AOS Short` and `AOS Video Processing Job`.
4. Frappe enqueues `aos.tasks.video_processing.dispatch_video_processing_job` to Frappe Redis Queue.
5. The dispatcher posts the job to `aos-video-api`.
6. `aos-video-api` enqueues the FFmpeg job into the video service Redis/RQ queue.
7. `aos-video-worker` processes the video and uploads outputs to MinIO.
8. `aos-video-worker` persists the terminal result and enqueues callback delivery on the dedicated callback queue.
9. `aos-video-callback-worker` calls `aos.api.v1.video_processing.handle_callback`.
10. Frappe stores validated visual classification evidence, updates `AOS Short`, creates thumbnail media metadata, and marks the job ready/failed.
11. Metadata publication fuses visual evidence with caption/hashtags and trusted ad context.

## Docker services

- `video-redis`: Redis instance dedicated to the video service queue.
- `video-api`: private FastAPI service, exposed only on host localhost.
- `video-worker`: horizontally scalable RQ workers with FFmpeg installed.
- `video-callback-worker`: lightweight RQ worker dedicated to terminal callback delivery/retries.

## Required env

```env
VIDEO_SERVICE_URL=http://127.0.0.1:8130
VIDEO_SERVICE_SECRET=change-this-long-random-video-dispatch-secret
VIDEO_SERVICE_CALLBACK_SECRET=change-this-long-random-video-callback-secret
VIDEO_CALLBACK_URL=https://api.example.com/api/method/aos.api.v1.video_processing.handle_callback
VIDEO_REDIS_URL=redis://video-redis:6379/0
VIDEO_QUEUE_NAME=video
VIDEO_CALLBACK_QUEUE_NAME=video-callbacks
VIDEO_WORKER_REPLICAS=2
VIDEO_CALLBACK_WORKER_REPLICAS=2
VIDEO_JOB_TIMEOUT_SECONDS=7200
VIDEO_FFMPEG_TIMEOUT_SECONDS=3600
VIDEO_MAX_DURATION_SECONDS=600
VIDEO_STALE_HEARTBEAT_SECONDS=180
VIDEO_MAX_STALE_WORK_REPLAYS=2
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

### RQ scheduler requirement

`video-worker` uses delayed retry intervals. Run it with `rq worker --with-scheduler`; otherwise scheduled processing or callback retries are never promoted back to the queue. In Docker Compose, rebuild and recreate the worker after changing this command.


## Capacity model

An RQ worker executes one heavy video job at a time. `VIDEO_WORKER_REPLICAS` is therefore the heavy-work concurrency knob; `VIDEO_CALLBACK_WORKER_REPLICAS` independently scales completion delivery; size it from measured 95th-percentile processing time, expected uploads/minute, CPU availability, and queue-lag SLOs rather than from user-count alone. Keep FFmpeg threads bounded per job so horizontal workers do not oversubscribe the host.

The repository Compose file is a single-host production-rehearsal topology, not a million-user storage architecture. Its single MinIO container/volume is a throughput and availability boundary. At large scale, run the worker tier under an orchestrator with autoscaling and use a horizontally scalable S3-compatible object-store deployment (or managed object storage) plus CDN/edge delivery for outputs. Track queue depth, oldest-job age, processing latency, worker failures, stale-work replays, callback backlog, object-store latency, and temporary-disk pressure.

Large Shorts now use a resumable multipart contract (currently from 16 MiB) with direct-to-object-storage part PUTs. Frappe stays out of the byte path, clients can resume only missing/invalid parts, and private raw Shorts assemble directly at their canonical key to avoid a second full-object copy. The repository's single-host MinIO topology is still a deployment capacity boundary, not a million-user storage architecture.

## Short admission contract

The client must send `duration_seconds` to `aos.api.v1.media.init_upload` when `purpose=short_video_raw`. Current backend admission is 300 MiB / 600 seconds. Rejecting at init prevents unsupported content from consuming upload bandwidth, while the processor's ffprobe validation remains the authoritative post-upload defense against a dishonest or inaccurate duration hint.

## Capacity sizing from heavy-upload rehearsal

Do not derive video-worker replicas from registered-user count. Derive them from the **arrival rate of Shorts that require processing** and the measured processing service time for representative media on production-equivalent hardware.

After deploying the resumable-upload backend to staging, first run `infra/load-testing/k6/short-upload.js` at low concurrency with a representative large fixture and both `RUN_SHORT_CREATE=true` and `WAIT_FOR_PROCESSING=true`. Low concurrency keeps queue wait close to zero so `create -> ready` is a useful approximation of processing service time. Set `TARGET_UPLOADS_PER_MINUTE` to the peak processing arrival rate you need to sustain. The script reports average- and p95-based starting replica estimates using:

`workers = ceil(target_uploads_per_minute × processing_seconds / (60 × target_utilization))`

Prefer the p95 estimate initially and keep target utilization below 1 (the rehearsal defaults to 70%) so bursts, retries, noisy inputs and callback work do not immediately create unbounded queue age. Then rerun at the intended aggregate arrival rate (preferably across a pool of staging users) and verify queue age remains bounded. The final production replica/autoscaling policy must also satisfy CPU, RAM, object-store/network throughput, queue oldest-job-age and failure/retry SLOs.
