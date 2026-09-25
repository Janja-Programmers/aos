# Moderation

## Overview
AOS Moderation is an internal safety capability for public/user-generated content. It combines automatic text and visual evidence with a single AOS policy and Frappe Desk human review. It is not a customer-facing product API.

## Ownership
Moderation owns the canonical taxonomy, normalized safety signals, policy version, automatic decision, moderation job/case, provider abstraction, retry/failure state and human-review audit. Ads, Reviews and Shorts remain authoritative for their lifecycle, visibility, ownership and publication state. Media owns stored objects; Video Processing owns video processing; Reports owns user complaints; Notifications owns delivery.

## Internal/Public Surface
Ordinary web/mobile clients do not call Moderation. The companion service is private. The only Frappe callback is `aos.api.internal.moderation.handle_callback`, a timestamped HMAC-authenticated service callback. Staff review uses `aos.api.internal.moderation.review` from Frappe Desk and requires DocType write permission.

**Postman phase: NOT APPLICABLE.**

**Customer-facing web phase: NOT APPLICABLE.**

## Integrated Features
Current automatic consumers are Ads, Reviews and Shorts. Profiles, Sellers and Live do not currently have a hardened moderation lifecycle contract and are therefore not silently wired in. Chat private messages are not indiscriminately scanned. Reports remain a separate complaint domain and can be used as review context/priority without becoming proof of a policy violation. Notifications are emitted only by owning feature services. Activity does not receive internal moderation evidence.

## Canonical Safety Policy
`infra/moderation/app/policy.py` is the single decision policy. Detectors/providers emit evidence; the policy emits `allow`, `reject` or `review`. High-confidence severe violations cannot be averaged away by safe modalities. Missing required evidence or detector failure routes to manual review, never approval. Policy version is `MODERATION_POLICY_VERSION` and defaults to `aos-safety-2026-09-25-v1`.

## Taxonomy
Canonical machine categories are `sexual_explicit`, `pornography`, `nudity`, `weapons`, `violence`, `graphic_violence`, `profanity`, `hate`, `harassment`, `threats`, `self_harm`, `illegal_goods`, `dangerous_content`, `drugs`, `spam`, `scam`, `fraud`, and `other`. Category, severity, confidence and decision are separate concepts.

## Inputs / Modalities
Adapters submit bounded text items plus authoritative Media references. Shorts additionally submits Video Processing poster/storyboard assets. Moderation never accepts arbitrary client URLs. OCR/transcript evidence can be added as text items when hardened owning services produce it; absence of required evidence is not interpreted as safe.

## Text Moderation
`text_detector.py` performs Unicode normalization, basic leetspeak normalization, punctuation/spacing evasion handling and token/phrase boundary matching. It avoids substring matching and records only canonical matched evidence labels, not full user text. This detector is one evidence source; it is not the whole moderation system.

## Image Moderation
The moderation worker reads bounded authoritative MinIO image objects and calls a private HMAC-authenticated OpenCLIP provider endpoint in image-search. The provider returns normalized safety category/confidence/severity signals. AOS Moderation, not OpenCLIP, makes the final decision. Provider unavailability routes the case to review.

## Video/Audio Moderation
Moderation does not build a second video-processing pipeline. Shorts consumes representative poster/storyboard output from hardened Video Processing. A video with no representative visual evidence is held for manual review. There is no current real-time Live audiovisual moderation and no current transcription/OCR service contract exposed to Moderation; those limitations are explicit. When a hardened transcript exists it should be supplied as text evidence.

## Multimodal Aggregation
Text and vision signals are combined by the canonical policy. Policy checks each severe signal independently; a high-confidence severe image/video-frame signal rejects even when all text is safe. Borderline evidence produces manual review. Required missing modalities and detector failures also produce review.

## Confidence
Thresholds are centralized by category in `policy.py`; feature modules contain no safety thresholds. Threshold changes require a policy-version change. OpenCLIP scores are evidence rather than claims of perfect probability calibration.

## Decision Model
Final moderation decisions are `allow`, `reject`, `review`. Job processing states are `Queued`, `Dispatching`, `Processing`, `Allowed`, `Review Required`, `Rejected`, `Failed`, `Cancelled`; service failure is not rejection and never approval. Decision source is `automatic` or `manual`.

## Manual Review
`AOS Moderation Job` is the Desk case/queue. Review-required cases expose target, owner, content version, policy version, normalized signals, confidence/risk, bounded reasons and retained source items. Staff Approve/Reject actions lock the case row and delegate the actual lifecycle transition to the owning Ads/Reviews/Shorts service.

## Content Versioning
Every job stores `content_fingerprint`, `content_version`, and a unique `evaluation_key` derived from target + fingerprint + policy version. Ads callbacks require the exact current `modified` version. Reviews use moderation generation. Shorts use revision + moderation generation. Stale work is discarded and deleted targets are never resurrected.

## Policy Versioning
Each evaluation stores the policy version. Frappe rejects a callback whose policy version does not match the requested job. Re-evaluating unchanged content under a new policy produces a distinct logical evaluation key.

## Provider/Model Architecture
Text rules are `aos_text_rules:2`. Vision uses the self-hosted image-search OpenCLIP runtime through an authenticated adapter. Provider output is normalized before policy evaluation. Provider credentials are environment secrets and are excluded from model/job payloads, Desk fields and logs.

## Reports Integration
Report Reason remains the reporter's allegation. Moderation Category is the moderation determination. Report count is not automatic guilt; current Reports does not directly mutate moderation decisions.

## Transactions
Frappe records business state + moderation intent/outbox in short transactions. Network/ML work occurs after commit in companion workers. Callback/manual application uses short row locks/version checks. No model request is performed while holding a Frappe row lock.

## Jobs / Queues
Frappe persists `AOS Moderation Job`, durable outbox dispatches to the private moderation API, and Redis/RQ runs bounded work. Stable evaluation/idempotency identity makes retries safe. Callback delivery is durable and retryable. Video processing is not duplicated inside this queue.

## Idempotency / Concurrency
`evaluation_key` is unique in MariaDB and `idempotency_key` uses the same stable hash. Duplicate creators converge on the database winner. Callback handling locks the job. Staff review locks the job first. Automatic retries cannot overwrite a completed manual case. Owning feature version/generation checks protect edit-vs-evaluation races.

## HA / Scaling
Correctness uses shared MariaDB, Redis/RQ, object storage and durable outbox state; it does not depend on process-local locks/caches. Worker count can scale horizontally. Input counts/bytes are bounded. Queue backlog never means approval. This architecture is suitable for horizontal scale, but 1M-user capacity is not claimed without load/soak testing and model-throughput sizing.

## Security
Clients cannot submit moderation decisions. Companion requests and callbacks use HMAC authentication and callback timestamps. Media is read only through authoritative stored bucket/object references. Provider hosts are allowlisted in staging/production. Staff decisions require DocType write permission. Secrets and full content are not logged.

## Privacy / Retention
The job stores only the bounded content needed for dispatch plus normalized evidence/audit metadata. Full request/response payload fields were removed. Existing service-job cleanup prunes moderation text/media/context payload fields according to service-job retention settings; minimal decision/audit fields remain. Temporary image bytes exist only in worker memory/provider request scope.

## Operational Health
`/health` reports process liveness. `/ready` requires moderation Redis and, when media inspection is enabled, configured/reachable vision inference readiness. Existing AOS operational health tracks the moderation companion separately from application liveness. Durable lifecycle metrics expose queue/work/callback state; companion metrics expose latency, errors and dependency readiness.

## Data Model
`AOS Moderation Job` is the durable evaluation/case record with opaque hash naming. Important indexed access paths are review queue (`status, creation`), target history (`target_doctype, target_name, creation`) and policy/status (`policy_version, status, creation`). `evaluation_key` is unique.

## Cross-feature Dependencies
Moderation consumes Media, Ads, Reviews, Shorts, Video Processing outputs, transactional outbox, Notifications through owning features, search-index refreshes through owning features, and operational health. It does not own those domains.

## Testing
Pure companion tests cover policy, text evasion/false positives, multimodal precedence, missing evidence and provider failures. Image-search tests mock embedding boundaries rather than live model downloads. Frappe tests cover unique logical evaluations, content-version/stale callbacks, callback policy version, Desk authorization/manual precedence, schema indexes and owning-feature integration. Full acceptance is `bench --site <site> migrate` then `bench --site <site> run-tests --app aos`, plus companion/image-search test suites. Tests must use hardened valid fixtures and transactional cleanup.

## Performance / Scalability
Text/media item counts, text length, image count/bytes, provider timeout, retry count and queue jobs are bounded. There is no synchronous heavy inference in customer HTTP creation paths. Vision work may be scaled independently by scaling image-search/OpenCLIP workers and moderation workers.

## Caching
No process-local cache is correctness authority. OpenCLIP prompt vectors/model instances are worker-local performance caches only; policy version and durable decisions live in shared state.

## Responsibilities
The moderation domain provides classification normalization, one policy, decisions, cases, human review coordination and auditability; owning domains apply their own state transitions.

## Boundaries
No public moderation inference API exists. Internal callback/staff methods are not a mobile/web contract. Live audiovisual moderation, OCR and speech transcription are not falsely claimed as implemented.

## Architecture
`owning feature -> persistent moderation job/outbox -> private moderation API/RQ -> text + vision evidence -> canonical policy -> signed callback -> owning feature transition / Desk review`.

## Fields
See `AOS Moderation Job`: target identity, source, evaluation key, content fingerprint/version, policy version, processing status, decision/source/reviewer/time, retry/error fields, normalized signals/scores/reasons, model versions, missing evidence, bounded source items and timing fields.

## API
There is no public v1 Moderation API. Internal service callback: `aos.api.internal.moderation.handle_callback`. Internal staff action: `aos.api.internal.moderation.review`.

## Transaction / Concurrency Model
See Transactions and Idempotency / Concurrency above; all authoritative race protection is durable database state/versioning, never Python-local locks.
