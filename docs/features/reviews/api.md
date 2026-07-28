# Reviews API v1

All AOS payloads use `{ok, message, data}` or `{ok, message, error, data}` and are returned inside Frappe's `message` response property. Clients branch on machine fields, never message text. Authenticated requests use the normal Frappe session cookie. JSON bodies cannot set reviewer, seller, target trust, lifecycle, moderation or aggregate fields.

| Endpoint | Method | Auth | Purpose |
|---|---:|---:|---|
| `aos.api.v1.reviews.get_review_viewer_state` | GET | Optional | Server-derived eligibility for an Ad |
| `aos.api.v1.reviews.create_review` | POST | Required | Create Pending review and moderation job |
| `aos.api.v1.reviews.update_review` | POST | Required | Owner edit; returns to Pending |
| `aos.api.v1.reviews.delete_review` | POST | Required | Idempotent soft withdrawal |
| `aos.api.v1.reviews.get_review` | GET | Optional | Public Approved detail or owner private detail |
| `aos.api.v1.reviews.list_reviews` | GET | Optional | Public Ad reviews and distribution |
| `aos.api.v1.reviews.list_my_reviews` | GET | Required | Author lifecycle history |
| `aos.api.v1.reviews.list_reviews_received` | GET | Required seller | Approved reviews across current seller's Ads |
| `aos.api.v1.reviews.toggle_reaction` | POST | Required | Add, remove or switch Like/Dislike |
| `aos.api.v1.reviews.report_review` | POST | Required | Idempotent abuse report using central Report Reasons |

There is no reply endpoint in the current domain model. Review images are uploaded through existing Media endpoints with `purpose=review_image`, then supplied as `MEDIA-*` IDs to create/update.

Supported public sorts are `newest`, `helpful`, `rating_high`, and `rating_low`. Self-list additionally supports `oldest`. Limits are 1–50 and offsets are bounded to 10,000. Integer pagination is strict; booleans, floats and ambiguous strings are rejected.

## Legacy v1 compatibility

The four original methods—create, public list, viewer state and reaction toggle—preserve legacy errors. Hardened canonical failures are included in `data.canonical_error`; eligibility includes `reason_code`. New methods return canonical errors directly. Create keeps flat `id`, `status`, `images`, moderation job fields and also returns the canonical nested `review`.
