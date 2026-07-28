# Reviews implementation report

## Cumulative baseline

The implementation used the cumulative backend root `current_backend/` extracted from `current_backend_wishlist_transport_fixed.zip`. The baseline contains the existing production foundation, Localization, Media, Accounts, Catalog, Ads and Wishlist hardening, their migrations, tests, documentation, CI/security gates and infrastructure. The cumulative files were retained; Reviews was not rebuilt from an older snapshot.

## Reviews architecture reviewed

The repository-wide trace covered unversioned and v1 APIs, Review/Reaction/Image DocTypes, Ads and Seller aggregate fields, Accounts public identity, chat communication evidence, Media attachment policy, central reports/reasons, blocking, account deletion, moderation jobs/callbacks, Notifications, notification delivery, transactional outbox, public error mapping and endpoint rate-limit coverage.

## Business rules discovered

- Relationship: authenticated user reviews an Ad; approved ratings also contribute to its Seller.
- Eligibility: one review after canonical AOS communication with the ad seller through Conversation/Message records.
- No completed Order, Booking, refund, dispute or fulfilment model is connected to Reviews.
- Scale: required whole-star rating from 1 to 5, plus required bounded title and comment.
- Media: up to five `review_image` Media objects.
- Lifecycle: Pending moderation, Approved, Rejected, Hidden or Withdrawn.
- Reactions: Like/Dislike, one active reaction per user/review.
- Reporting: central active `AOS Report Reason`, private moderator queue, no reporter-controlled visibility change.
- No seller-reply entity or endpoint existed, so one was not invented.

`verified_interaction` therefore means server-verified AOS communication, not verified purchase.

## Problems found

- Business logic and validation were distributed across API and DocType modules.
- Client payloads were not uniformly strict and Reviews wrappers did not consistently strip Frappe transport metadata.
- Rating conversion accepted ambiguous numeric input; pagination also accepted coercible floats/bools.
- Text validation did not adequately address control/invisible characters, unsafe schemes and common spam patterns.
- Duplicate prevention lacked one canonical nullable key and a safe non-destructive legacy reconciliation policy.
- Moderation callbacks could apply an older job after a review edit.
- Aggregate calculation was duplicated and lacked bounded Ad/Seller reconciliation.
- Reaction/report uniqueness and deterministic duplicate handling were incomplete.
- Detail, edit, withdrawal, author-history, received-history and review-reporting contracts were absent.
- Public/self serialization and deleted-account retention policy were not explicit enough.
- Review moderation notifications were not canonicalised for author/seller delivery.
- Feature tests, API guidance and operations documentation were incomplete.

## Changes implemented

- Added a cohesive Reviews application service with thin API wrappers.
- Centralised strict field, identifier, rating, text, Media, reaction, report and pagination validation.
- Derived reviewer, target, seller and interaction evidence exclusively on the server.
- Enforced self-review, block, seller-state and duplicate policies.
- Added deterministic review uniqueness plus transaction-safe row locking and idempotent retries.
- Added owner-only edit, idempotent soft withdrawal, safe Media attach/release and re-moderation generation increments.
- Added public detail/list, private author list, seller-received list, reaction and report operations.
- Added privacy-safe serializers based on Accounts public identity primitives.
- Added generation-correlated moderation callback handling; stale callbacks are harmless no-ops.
- Added canonical review-approved/rejected/received notifications through persistent Notifications and notification-delivery outbox work.
- Added central Ad/Seller aggregate and reaction-count helpers plus dry-run/repair reconciliation.
- Added deleted-account review retention/anonymisation and private action cleanup.
- Added Review Report DocType integrated with existing Report Reasons and moderator-only state changes.
- Added additive, idempotent migration/index/unique-constraint hardening with bounded processing and non-destructive Review duplicate handling.
- Preserved legacy v1 errors and flat create fields while exposing canonical codes for new clients.
- Added operation-specific rate limits, stable error mappings, bounded operational events, tests and professional documentation.

## Security and abuse prevention

The implementation blocks client-supplied reviewer/target trust fields, unrelated/self/blocked reviews, cross-user Media, duplicate review/reaction/report rows, non-approved review reactions/reports, self-voting/reporting, unknown sort/fields, unbounded pagination, unsafe text and stale moderation callbacks. Reporting never grants direct moderation power. Public APIs do not expose reporter identity, private communication evidence, private account data, internal moderation/provider data or storage identifiers.

## Data-integrity and aggregate strategy

Approved Review rows are canonical. Ad and Seller denormalised rating/count fields are recalculated from that source on lifecycle changes. Hidden, Rejected and Withdrawn rows are excluded. Reaction counts are rebuilt from canonical reaction rows. The migration and operator reconciliation functions are rerunnable and report drift before repair. Legacy duplicate Reviews are retained as Withdrawn audit history rather than deleted.

## Privacy decisions

Historical reviews survive account deletion for marketplace trust, but Accounts renders the author as `Deleted User`. Private reaction and report rows created by the deleted account are removed. Seller deletion removes active Seller/Ad discovery without granting review-history manipulation. Authors receive only stable moderation codes and generic product-safe guidance, never raw moderator/provider notes.

## Compatibility decisions

Existing public v1 methods and HTTP methods remain. The original machine errors and flat create response fields remain available. Canonical hardened errors are provided in `data.canonical_error`, and eligibility includes `reason_code`. Unversioned implementation modules remain internal. Old Media/constants helper modules are retained as thin delegates rather than duplicate policy owners.

## Remaining risks

Communication evidence does not prove a completed purchase. A future canonical Order/Booking fulfilment model should replace or augment eligibility through a versioned migration. The repository still has no review-reply feature. Full Frappe/MariaDB concurrency, migration and cross-feature behaviour must be validated on staging because the artifact environment has no Bench/Frappe runtime or service dependencies.
