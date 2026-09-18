# Reviews API test guide

Base URL:

```text
https://<host>/api/method/
```

Authenticated requests use a valid Frappe `sid` cookie. POST requests use `Content-Type: application/json`. Replace all sample IDs with records from the test site. Responses are wrapped by Frappe under `message`; the AOS payload inside it is either `{ok, message, data}` or `{ok, message, error, data}`.

## Contract compatibility

The four pre-existing v1 methods preserve their original machine errors:

- `create_review`
- `list_reviews`
- `get_review_viewer_state`
- `toggle_reaction`

For those methods, an old client may receive `ALREADY_REVIEWED`, `OWN_AD`, `CONTACT_SELLER_REQUIRED`, `NOT_FOUND`, or `VALIDATION_ERROR`. The hardened canonical error is also returned in `data.canonical_error`. Eligibility success data similarly keeps legacy `reason` and adds canonical `reason_code`. New clients should prefer `reason_code` and `data.canonical_error` while continuing to branch only on machine-readable fields, never message text.

## Shared setup

1. Create active buyer and seller users.
2. Create an `Active` AOS Seller owned by the seller user.
3. Create an `Active` AOS Ad owned by that seller.
4. Create an AOS Conversation between buyer and seller and at least one AOS Message. Viewing an ad alone is not eligible.
5. Keep a third active user for reaction and reporting tests.
6. For public-review tests, complete the existing moderation callback flow so a Pending review becomes Approved.

The current backend has no completed Order or Booking model attached to Reviews. The verified interaction badge means server-verified AOS communication, not verified purchase.

## 1. Eligibility check

**Path:** `aos.api.v1.reviews.get_review_viewer_state`  
**Method:** GET  
**Authentication:** Optional  
**Headers:** `Cookie: sid=<buyer-session>` when testing an authenticated viewer  
**Query:**

```text
?ad_id=ad_replace_with_public_id_1
```

**Success:**

```json
{
  "ok": true,
  "message": "Review viewer state fetched.",
  "data": {
    "can_review": true,
    "reason": null,
    "reason_code": null,
    "has_reviewed": false,
    "has_communicated": true,
    "existing_review_id": null,
    "existing_review_status": null,
    "eligibility_basis": "communication"
  }
}
```

**Stable outcomes:** `LOGIN_REQUIRED`, `AD_NOT_FOUND`, `SELLER_INACTIVE`, `REVIEW_SELF_NOT_ALLOWED`, `USER_BLOCKED`, `TRANSACTION_NOT_ELIGIBLE`, and `REVIEW_ALREADY_EXISTS`. The private conversation identifier is never returned.

## 2. Review Media attachment preparation

Review images use the existing Media init/upload/confirm API with `purpose=review_image`. The confirmed Media must:

- belong to the authenticated reviewer;
- be ready and attachable;
- have `review_image` purpose;
- not already be attached incompatibly.

Save the returned `MEDIA-...` ID. Create/update accepts at most five unique Media IDs. Raw URLs, cross-user Media, wrong-purpose Media and unconfirmed Media fail with `INVALID_REVIEW_MEDIA` or `MEDIA_ACCESS_DENIED`. Reviews never accepts MinIO keys or client-provided public URLs.

## 3. Create review

**Path:** `aos.api.v1.reviews.create_review`  
**Method:** POST  
**Authentication:** Required buyer session  
**Headers:** `Content-Type: application/json`, `Cookie: sid=<buyer-session>`  
**Body:**

```json
{
  "ad_id": "ad_replace_with_public_id_1",
  "rating": 5,
  "title": "Great seller",
  "comment": "Accurate listing and good communication.",
  "images": ["MEDIA-0123456789abcdef0123456789abcdef"]
}
```

**Success:** HTTP 200 with a Pending review, flat compatibility fields, a rich `review` object, moderation job identifiers, and an updated viewer state. Representative data:

```json
{
  "id": "RVW-2026-00001",
  "status": "Pending",
  "images": [],
  "review": {
    "id": "RVW-2026-00001",
    "ad_id": "ad_replace_with_public_id_1",
    "rating": 5,
    "status": "Pending",
    "can_edit": true,
    "can_delete": true
  },
  "moderation_job_id": "<opaque-moderation-job-id>",
  "moderation_job_status": "Queued"
}
```

**Stable failures:** `AUTH_REQUIRED`, `INVALID_REVIEW_REQUEST`, `INVALID_RATING`, `INVALID_REVIEW_TITLE`, `INVALID_REVIEW_TEXT`, `INVALID_REVIEW_MEDIA`, `MEDIA_ACCESS_DENIED`, `REVIEW_SELF_NOT_ALLOWED`, `TRANSACTION_NOT_ELIGIBLE`, `USER_BLOCKED`, `SELLER_INACTIVE`, `REVIEW_ALREADY_EXISTS`, `RATE_LIMIT`, and safe dependency/internal errors. Unknown or server-owned fields such as `reviewer`, `seller`, `status`, `verified_purchase` and `cmd` are not accepted as client business data; only Frappe-owned transport `cmd` is stripped by the v1 wrapper.

## 4. Review detail

**Path:** `aos.api.v1.reviews.get_review`  
**Method:** GET  
**Authentication:** Optional  
**Query:** `?review_id=RVW-2026-00001`

An Approved review is public. The author may also fetch their Pending, Rejected, Hidden or Withdrawn review and receives safe lifecycle/capability fields. Other users receive `REVIEW_NOT_FOUND` for every non-public state, preventing state enumeration. Email, phone, private interaction evidence, reporter identities, moderation provider data and storage keys are absent.

## 5. Public review listing

**Path:** `aos.api.v1.reviews.list_reviews`  
**Method:** GET  
**Authentication:** Optional  
**Query example:**

```text
?ad_id=ad_replace_with_public_id_1&sort=helpful&rating=5&limit=20&offset=0
```

**Supported sorts:** `newest`, `helpful`, `rating_high`, `rating_low`.  
**Pagination:** limit 1–50; offset 0–10,000; deterministic secondary ordering by creation/name.  
**Success data:** `summary.average_rating`, `summary.total_reviews`, five-star distribution, public `reviews`, and `pagination`.

**Stable failures:** `REVIEW_TARGET_INVALID`/legacy `NOT_FOUND`, `INVALID_REVIEW_SORT`/legacy `VALIDATION_ERROR`, `INVALID_RATING`/legacy `VALIDATION_ERROR`, `INVALID_REVIEW_PAGINATION`/legacy `VALIDATION_ERROR`, and `RATE_LIMIT`.

## 6. My reviews

**Path:** `aos.api.v1.reviews.list_my_reviews`  
**Method:** GET  
**Authentication:** Required  
**Query example:**

```text
?status=Pending&sort=newest&rating=5&limit=20&offset=0
```

**Supported statuses:** `Pending`, `Approved`, `Rejected`, `Hidden`, `Withdrawn`.  
**Supported sorts:** `newest`, `oldest`, `rating_high`, `rating_low`.

Only the authenticated author's rows are returned. Safe private fields include status, a stable rejection reason code/generic product message, and edit/delete capabilities. Internal moderator notes are not returned.

## 7. Reviews received by seller

**Path:** `aos.api.v1.reviews.list_reviews_received`  
**Method:** GET  
**Authentication:** Required seller user  
**Query example:**

```text
?sort=rating_high&rating=5&limit=20&offset=0
```

The server resolves the seller from the current user. A client cannot supply another seller ID. Only Approved reviews are returned. A user without a seller receives `SELLER_REQUIRED`.

## 8. Edit review

**Path:** `aos.api.v1.reviews.update_review`  
**Method:** POST  
**Authentication:** Required original reviewer  
**Body:**

```json
{
  "review_id": "RVW-2026-00001",
  "rating": 4,
  "title": "Updated review",
  "comment": "Updated after further communication.",
  "images": []
}
```

Only provided editable fields change. An unchanged retry returns `idempotent=true`. A material change increments `moderation_generation`, records edit metadata, returns the review to Pending, safely attaches/releases Media and enqueues a new moderation job. The target, reviewer, eligibility evidence, status and aggregate fields are immutable client-side.

**Stable failures:** `REVIEW_NOT_FOUND`, `REVIEW_UPDATE_NOT_ALLOWED`, validation/Media errors, `RATE_LIMIT`, and safe internal/dependency failures. A callback from an older generation is accepted only as an idempotent job completion and cannot mutate the newer review.

## 9. Delete/withdraw review

**Path:** `aos.api.v1.reviews.delete_review`  
**Method:** POST  
**Authentication:** Required original reviewer  
**Body:**

```json
{"review_id": "RVW-2026-00001"}
```

**Success:** status becomes `Withdrawn`; repeated withdrawal returns the same outcome with `idempotent=true`. The review is excluded from public lists and aggregates, and attached Media is released through Media lifecycle APIs.

**Stable failures:** `REVIEW_NOT_FOUND`, `REVIEW_UPDATE_NOT_ALLOWED` for cross-user access, and `RATE_LIMIT`.

## 10. Helpful reaction

**Path:** `aos.api.v1.reviews.toggle_reaction`  
**Method:** POST  
**Authentication:** Required third-party user  
**Body:**

```json
{"review_id": "RVW-2026-00001", "reaction": "Like"}
```

First request adds; the same request removes; sending `Dislike` switches atomically. Success returns `status`, active `reaction`, `like_count`, and `dislike_count`. The database permits one reaction per user/review.

**Stable failures:** `REVIEW_NOT_FOUND`, `REVIEW_SELF_VOTE_NOT_ALLOWED` (legacy `VALIDATION_ERROR`), `INVALID_REVIEW_REACTION` (legacy `VALIDATION_ERROR`), `USER_BLOCKED`, `REVIEW_REACTION_CONFLICT`, and `RATE_LIMIT`.

## 11. Report review

First fetch configured reasons:

**Path:** `aos.api.v1.reports.list_report_reasons`  
**Method:** GET  
**Authentication:** Follow the existing Reports endpoint policy  
**Success data:** `reasons[]` containing the canonical `id`, title and icon key. Use the returned `id` exactly.

Then submit:

**Path:** `aos.api.v1.reviews.report_review`  
**Method:** POST  
**Authentication:** Required non-author user  
**Body:**

```json
{
  "review_id": "RVW-2026-00001",
  "reason": "Inappropriate Content",
  "details": "Contains repeated promotional links."
}
```

The reason must be an active `AOS Report Reason`. A same-user duplicate is idempotent. Reporting creates a private moderation record in `Reviewing` state; it never lets the reporter hide, delete or change the review. Reporter identity is not present in review APIs.

**Stable failures:** `REVIEW_NOT_FOUND`, `REVIEW_REPORT_NOT_ALLOWED`, `INVALID_REVIEW_REPORT_REASON`, `INVALID_REPORT_DETAILS`, `REVIEW_REPORT_CONFLICT`, and `RATE_LIMIT`.

## 12. Seller replies

There is no seller-reply entity or public reply endpoint in the current repository. No reply request should be sent. A future reply contract requires a dedicated versioned domain model, ownership policy and moderation lifecycle.

## Moderation callback checks

Use the existing signed moderation worker callback flow. Verify:

1. generation 1 approval publishes generation 1;
2. an edit creates generation 2 and returns Pending;
3. a delayed generation 1 callback cannot publish/reject generation 2;
4. duplicate generation 2 callbacks are harmless;
5. approval creates persistent author/seller notifications and notification-delivery outbox work;
6. rejection notifies only with generic safe product language;
7. invalid signatures, expired timestamps, wrong job/review correlation and replay are rejected by the shared moderation callback boundary.

## Cleanup

Withdraw isolated test reviews, remove test reactions/reports, and release orphan test Media through Media cleanup. Remove test conversations/messages/ads/sellers/users using the normal test-site teardown. Never run destructive cleanup against production records.
