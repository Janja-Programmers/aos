# Changed-files report

## Previously completed files retained

The complete cumulative baseline was retained, including Production Foundation, Localization, Media, Accounts, Auth, Catalog, Ads, Wishlist, moderation, notifications, transactional outbox, deployment/CI infrastructure, existing patches, tests and documentation. No project root was replaced by a Reviews-only patch.

## Reviews files added or materially rewritten

- `aos/services/reviews/__init__.py`
- `aos/services/reviews/constants.py`
- `aos/services/reviews/errors.py`
- `aos/services/reviews/validation.py`
- `aos/services/reviews/eligibility.py`
- `aos/services/reviews/service.py`
- `aos/services/reviews/serializers.py`
- `aos/services/reviews/aggregates.py`
- `aos/services/reviews/api.py`
- `aos/services/reviews/observability.py`
- `aos/api/reviews/create.py`
- `aos/api/reviews/update.py`
- `aos/api/reviews/delete.py`
- `aos/api/reviews/detail.py`
- `aos/api/reviews/list.py`
- `aos/api/reviews/my.py`
- `aos/api/reviews/viewer_state.py`
- `aos/api/reviews/toggle.py`
- `aos/api/reviews/report.py`
- `aos/api/reviews/eligibility.py`
- `aos/api/reviews/constants.py` — compatibility aliases to central rate policy.
- `aos/api/v1/reviews/__init__.py`
- `aos/aos/doctype/aos_review/aos_review.json`
- `aos/aos/doctype/aos_review/aos_review.py`
- `aos/aos/doctype/aos_review_reaction/aos_review_reaction.py`
- `aos/aos/doctype/aos_review_report/*`
- `aos/api/reviews/tests/*`
- `docs/features/reviews/*`

## Cross-feature files modified and why

- `aos/services/moderation_service.py` — moderation generation in job context; stale callback rejection; safe lifecycle application; canonical review notifications.
- `aos/services/notification_service.py` — review received/approved/rejected notification constructors.
- `aos/api/notifications/constants.py` — marketplace categorisation for review notification types.
- `aos/services/account_deletion_service.py` — retain/anonymise review trust history, remove private reactions/reports and repair reaction counts.
- `aos/api/shared/responses.py` — stable Reviews HTTP status/error mapping.
- `aos/patches/v1_0/add_unique_constraints.py` — removed the historical destructive Review duplicate cleanup/old composite constraint from fresh execution.
- `ci/public-endpoint-rate-limits.json` — explicit coverage records for every public Reviews wrapper.
- `aos/patches.txt` — registered the Reviews hardening patch.

## Migration added

- `aos/patches/v1_0/harden_reviews_subsystem.py`

The patch is additive, idempotent, install-safe, bounded, does not commit, preserves duplicate Review records by withdrawing later duplicates, and adds/rebuilds schema metadata, indexes, unique constraints and Approved-only Ad/Seller aggregates.

## Documentation added

- `README.md`
- `architecture.md`
- `api.md`
- `eligibility.md`
- `rating-model.md`
- `review-lifecycle.md`
- `moderation.md`
- `media-integration.md`
- `seller-integration.md`
- `privacy.md`
- `security.md`
- `aggregates.md`
- `operations.md`
- `testing.md`
- `migration.md`
- `api-test-guide.md`
- `migration-deployment-guide.md`
- `implementation-report.md`
- `changed-files.md`
- `test-results.md`

No Media storage implementation, Accounts serializer ownership, Seller lifecycle ownership, infrastructure secret, real environment file or public API version was removed.
