# Video Processing

## Overview
Private durable transformation pipeline consumed by Shorts. It is not a client-visible publication or moderation authority.

## Responsibilities
FFmpeg normalization, HLS, posters, storyboards, extracted audio, downloadable renditions, side-by-side and segment composition, technical status and cleanup of derived artifacts.

## Boundaries
Shorts owns post lifecycle/publication; Media owns originals, derived objects, authorization and object storage. The processing companion owns technical execution only. `aos/services/video_processing_service.py`, `aos/tasks/shorts.py`, `aos/api/internal/video_processing/`, `infra/video-processing/` are the integration boundaries.

## Architecture
Shorts writes `AOS Video Processing Job` and a transactional outbox event. A private signed companion accepts bounded jobs, processes on queues using isolated FFmpeg arguments, and returns a signed callback. The backend validates revision/generation, leases and result evidence before linking Media.

## Data Model
`AOS Video Processing Job` carries generation, active key, attempts, retry/lease state, callback correlation and terminal result. Outputs are registered as canonical Media and linked by the Shorts owner; technical status cannot imply moderation approval.

## Fields
Technical statuses: Queued, Processing, Retry Waiting, Ready, Failed and Cancelled. Input/upload object references and callback URLs are server-generated and scoped to job intent.

## API
Only `/api/method/aos.api.internal.video_processing.handle_callback` accepts signed service callbacks. The companion exposes private job/health/readiness and bounded reconciliation interfaces, not a public v1 client namespace. The creator starts work using [Shorts](../shorts/README.md) endpoints.

## Cross-feature Dependencies
Shorts, Media, Moderation, transactional outbox, Notifications as chosen by Shorts, and private object-storage credentials.

## Transaction / Concurrency Model
A unique active key, work generation, lease claim, bounded retries and stale-callback suppression prevent duplicate publication across workers. Never publish outputs or dispatch work before the owning DB transaction commits; re-check authoritative Short state on callback.

## Security
Verify request signatures, host allowlists, size/type/duration limits, storage prefixes and output manifests. Avoid shell interpolation, raw credentials in logs, arbitrary URL fetches or public administrative companion routes.

## Caching
Immutable output keys and Media-backed rendition caching. Files local to a worker are scratch only, not cross-node correctness state; failed-job artifacts require bounded cleanup.

## Performance / Scalability
Separate video queues from request workers, set encoder/process concurrency, limits and timeouts, and monitor retry/dead-letter/backlog. Benchmark actual CPU/GPU, object-store bandwidth and peak creation volume before capacity claims.

## Testing
`infra/video-processing/tests/`, `aos/tests/test_shorts_hardened_contract.py` and Shorts database/callback tests cover replay, stale generation, invalid manifest, retries, worker failure and Media ownership. Validate restart recovery with concurrent real processors and object storage.
