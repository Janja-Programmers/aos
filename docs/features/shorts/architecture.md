# Architecture

## Components

- `aos/api/v1/shorts`: stable HTTP methods and decorators.
- `aos/services/shorts`: validation, policies, repositories, media rules, feeds, analytics, notification decisions, serializers, errors and observability.
- `aos/api/shorts`: compatibility implementations retained for callers already importing them.
- `aos/services/video_processing_service.py`: persistent job/outbox orchestration.
- `infra/video-processing`: authenticated, durable FFmpeg companion and representative-frame sampler.
- `infra/image-search`: private OpenCLIP frame classifier shared with visual ad search.
- `AOS Media Object`: canonical upload ownership and object lifecycle.
- `AOS Transactional Outbox`: atomic dispatch and callback lifecycle.

Dependency direction is v1 wrapper -> Shorts service -> repositories/shared services. Compatibility modules call the same policy helpers and contain no transaction commits.

## Authorization boundary

Visibility, comment, download and owner rules are centralized in `aos.services.shorts.policy`. Feed SQL applies the same base predicates, and serializers perform a batched defense-in-depth filter before returning rows.

## Transaction boundary

Frappe owns request/job transactions. Shorts mutations create a savepoint, restore callback manager state when rolling back a handled error, and leave outer commit/rollback to Frappe. Outbox rows and domain mutations are therefore atomic.

## Classification boundary

The video companion sends bounded frames to the private OpenCLIP service using `SHORT_CLASSIFICATION_SECRET`. Frappe never imports Torch/OpenCLIP. Visual scores are evidence only; Frappe owns the final fusion, Shop/ad authorization, persistence, moderation handoff, and public serialization.
