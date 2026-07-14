# AOS Content Moderation Service

Phase 2 separates content analysis from Frappe while keeping Frappe as the business source of truth.

## Runtime shape

```text
Frappe API
  -> creates business record
  -> creates AOS Moderation Job
  -> enqueues Frappe dispatcher on long queue
  -> dispatcher POSTs signed job to moderation-api

moderation-api
  -> verifies X-AOS-Moderation-Signature
  -> enqueues job to moderation Redis/RQ

moderation-worker
  -> analyzes text/media signals
  -> POSTs signed callback to Frappe

Frappe callback
  -> verifies X-AOS-Moderation-Callback-Signature
  -> stores labels/scores/reasons on AOS Moderation Job
  -> applies final business decision to the target record
```

## Frappe owns decisions

The external service returns signals and a recommendation:

```json
{
  "decision": "allow",
  "labels": ["text_present", "image_inspected"],
  "scores": {"image_inspected": 0.05},
  "reasons": [],
  "risk_score": 0.05
}
```

Frappe applies that recommendation to business records:

- AOS Ad: `allow -> Active`, `review -> Reviewing`, `reject -> Declined`
- AOS Review: `allow -> Approved`, `review -> Pending`, `reject -> Rejected`
- AOS Short: `allow -> visible/auto_approved`, `review -> hidden/flagged`, `reject -> hidden/rejected`

## Current analyzer

The first moderation worker is deterministic and rule based:

- text reject terms
- text review terms
- basic image inspection through MinIO/Pillow
- video/media presence signals

This is intentional. ML models can replace the worker internals later without changing Frappe APIs, DocTypes, or business lifecycle code.

## Private service

The service is private and bound to localhost through Docker Compose:

```text
http://127.0.0.1:8140
```

It does not need a public domain.

## Health checks

```bash
curl http://127.0.0.1:8140/health
curl http://127.0.0.1:8140/ready
```

## Important env vars

```env
MODERATION_ENABLED=true
MODERATION_FAIL_OPEN=false
MODERATION_SERVICE_URL=http://127.0.0.1:8140
MODERATION_SERVICE_SECRET=...
MODERATION_SERVICE_CALLBACK_SECRET=...
MODERATION_CALLBACK_URL=https://api.example.com/api/method/aos.api.v1.moderation.handle_callback
MODERATION_REDIS_URL=redis://moderation-redis:6379/0
MODERATION_QUEUE_NAME=moderation
```
