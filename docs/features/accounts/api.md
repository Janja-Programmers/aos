# Accounts API

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `get_my_preference` | GET | Session required | Client |
| `get_profile` | GET | Session required | Client |
| `update_my_preference` | POST | Session required | Client |
| `update_profile` | POST | Session required | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

## Overview

Accounts owns the AOS product profile and the authenticated APIs that read or mutate it. It also exposes the authenticated user's persisted localization preference through a thin Accounts boundary. The canonical public account identifier is the immutable `AOS Profile.name` value in `ACC-*` form. The Frappe `User.name`/email remains an internal authentication identity and is never accepted as an Accounts public profile identifier.

Accounts does not own login, sessions, passwords, 2FA, email verification, password reset, or delete/restore authentication. Authentication owns those flows. Localization owns country/currency/language/location validation and persistence semantics. Media owns upload/storage lifecycle and media authorization. Verification owns verification state. Sellers owns seller state. Social owns follow/block state. Ads owns the persisted market on each listing.

All public Accounts responses use the standard AOS envelope: success is `{ok, message, data}` and failure is `{ok, message, error, data}`. Clients branch on the stable `error` code rather than message text.

## Architecture

```text
Client
  -> aos.api.v1.accounts
  -> strict transport/request contract
  -> Authentication session/account-state guard
  -> shared Redis rate limiter
  -> Accounts service
       -> AccountRepository / AOS Profile
       -> Localization preference service / AOS User Preference
       -> Media authorization and attachment service
       -> Social relationship repository
       -> Seller / Verification read projections
  -> explicit serializer allowlist
  -> AOS response envelope
```

The versioned API wrappers strip only Frappe-owned transport metadata such as `cmd`. Domain request fields remain visible to Accounts so unknown client fields can be rejected. Endpoint functions are orchestration-only; profile rules, data access, serialization, identity resolution, and localization semantics live in reusable services.

## Public identity

`AOS Profile.name` is the account's public immutable identifier. It has the form `ACC-` followed by a 20-character opaque Base32 token. Requests that identify another account accept only this identifier. Email addresses and Frappe `User.name` are not aliases for public account IDs.

Internally, `AOS Profile.user` links the profile to the Frappe `User` authentication record. Backend-only serializers may carry that internal identity while joining other features, but public Accounts responses never expose it as an account identifier.

## Data layer

### `AOS Profile`

`AOS Profile` is the Accounts aggregate root for product-profile state. Cardinality is exactly one profile per managed AOS user. `name` is the immutable public account ID; `user` is a unique internal Link to Frappe `User`. Creation is part of Authentication account bootstrap. Read paths never create or repair a missing profile.

| Field | Required / ownership | Purpose and mutability |
|---|---|---|
| `name` | Required, server generated | Immutable opaque `ACC-*` public account ID and primary key. |
| `user` | Required, unique, server controlled | Internal Link to the Frappe authentication identity. Never client mutable. |
| `display_name` | Required, owner editable | Public display name, NFC-normalized, 2-80 characters. Frappe User name fields are only framework projections. |
| `legal_name` | Optional, owner editable/private | Owner-only legal name, up to 160 characters. |
| `bio` | Optional, owner editable/public | Public profile biography, normalized and limited to 500 characters. |
| `phone` | Optional, owner editable/private | Owner-only normalized international phone number. |
| `date_of_birth` | Optional, owner editable/private | Owner-only date; future and implausibly old dates are rejected. |
| `gender` | Optional, owner editable/private | Owner-only value from the supported bounded set. |
| `profile_image_media` | Optional, controlled through Accounts+Media | Link to an owned `profile_image` Media object attached to this profile. Clients set `avatar_media_id`, not this field directly. |
| `account_status` | Server controlled | `Active`, `Suspended`, or `Deleted`. Authentication/account lifecycle owns transitions. |
| `deleted_at`, `restore_deadline`, `delete_reason`, `lifecycle_reason`, `restored_at` | Server controlled | Recoverable-deletion lifecycle metadata. |
| `purge_status`, `purge_started_at`, `purge_completed_at` | Server controlled | Permanent-deletion workflow state. |
| `total_followers`, `total_following` | Server controlled | Denormalized Social counters for bounded profile reads. |
| `is_verified` | Server controlled/read projection | Verification display state; clients cannot set it through Accounts. |

The primary key indexes `name`. `user` is unique, enforcing one profile per authentication identity and resolving concurrent bootstrap races at the database boundary. `profile_image_media`, `account_status`, `restore_deadline`, and `purge_status` have the indexes required by their current access paths. Accounts installs composite lifecycle indexes on `(account_status, restore_deadline)` and `(account_status, purge_status, restore_deadline)` for bounded lifecycle/purge scans.

### `AOS User Preference` dependency

`AOS User Preference` belongs to Localization, not Accounts. Accounts only exposes authenticated read/update APIs over Localization's service. There is exactly one row per user: `user` is required and unique and is also the document naming key. The relevant persisted fields are `country`, `currency`, `language`, and optional `location`.

Localization owns validation against Country/Currency/Language/AOS Location master data, row locking for mutation, shared-cache invalidation, and the country/location consistency invariant. Accounts does not create preferences during reads and does not make seller/ad/storefront decisions when preferences change.

## Endpoint contracts

### GET `/api/method/aos.api.v1.accounts.get_profile`

**Authentication:** required active/enabled account with required authenticated bootstrap state.

**Purpose / frontend usage:** with no request fields, load the current account/profile screen. With `account_id`, load another account's public profile surface.

**Request fields:** `account_id` is optional. If present it must be a canonical `ACC-*` ID. No aliases or additional client fields are accepted.

**Owner response:** contains `account_id`, `display_name`, `bio`, public avatar URL, social counts, `is_verified`, seller summary, owner email, `legal_name`, `phone`, `date_of_birth`, `gender`, `profile_image_media`, `account_status`, enabled/lifecycle purge state, four-field `preferences`, roles, verification summary, and `can_edit`. It does not expose Frappe `User.name`/`internal_user`.

**Public response:** explicit allowlist containing public account ID, display name, bio, public avatar URL, verification display flag, bounded social counts, public seller summary, and viewer-relative Social relationship state. It does not expose email, phone, legal name, date of birth, roles, enabled state, internal User identity, private media IDs, purge state, or private verification request details.

**Availability:** suspended, deleted, disabled, or missing target accounts are not returned as public profiles. A profile hidden by a block relationship returns `PROFILE_UNAVAILABLE` without exposing private account state.

**Rate limit:** self reads: 60/minute/user; target public-profile reads: 90/minute/user. The limiter uses the shared Frappe Redis cache and site-safe AOS keys.

**Caching:** self and target-profile responses are private/no-store. Target profiles include viewer-relative Social capabilities, so they are never placed in a shared HTTP cache. Cache is never an authorization source of truth.

**Side effects / transaction / retry:** read-only. It does not create profiles/preferences, repair rows, update localization, or commit. Retries are safe.

**Important errors:** `UNAUTHORIZED`, canonical account-state Auth errors, `PREFERENCE_MISSING`, `INVALID_PROFILE_FIELD`, `INVALID_ACCOUNT_ID`, `ACCOUNT_NOT_FOUND`, `PROFILE_UNAVAILABLE`, rate-limit errors, and `INTERNAL_ERROR`.

### POST `/api/method/aos.api.v1.accounts.update_profile`

**Authentication:** required active/enabled account with required preference state. Ownership is always derived from the authenticated session; no target account field exists.

**Purpose / frontend usage:** edit the current user's profile details or assign/remove the profile image.

| Field | Required | Validation |
|---|---|---|
| `display_name` | Optional | Normalized Unicode text; 2-80 characters. |
| `legal_name` | Optional | Normalized Unicode text; empty or 2-160 characters. |
| `bio` | Optional | Normalized text; max 500 characters. |
| `phone` | Optional | Empty or normalized international `+...` form. |
| `date_of_birth` | Optional | Empty/null or valid date from 1900 through today. |
| `gender` | Optional | Empty, `Male`, `Female`, `Other`, or `Prefer not to say`. |
| `avatar_media_id` | Optional | Media ID owned by the authenticated user and valid for `profile_image`. |
| `remove_avatar` | Optional | Remove request; cannot be combined with `avatar_media_id`. |

At least one accepted field is required. Unknown and server-controlled fields are rejected. Clients cannot mutate `account_id`, Frappe User identity/email, roles, enabled state, `account_status`, seller state, verification state, counters, lifecycle state, or the raw `profile_image_media` link.

**Media behavior:** Media validates ownership, purpose, attachment target, and lifecycle state. The profile row is locked before avatar replacement. A replacement attaches the new Media object, updates Profile and the Frappe `user_image` projection, and releases the previous attachment in one request transaction. Cross-account and wrong-purpose media are rejected.

**Concurrency / transaction:** the Profile row is selected `FOR UPDATE`, serializing concurrent profile/avatar mutations across application nodes. Profile/User/Media writes use the Frappe request transaction; there is no manual success commit. If a handled failure occurs after an earlier mutation, the endpoint rolls the request transaction back because it returns an error envelope rather than propagating an exception through Frappe.

**Idempotency:** identical profile assignments are no-ops where possible. Assigning the already-current avatar does not release/re-attach it. Database row locking prevents two concurrent avatar replacements from producing split profile state.

**Rate limits:** 20 profile updates/minute/user. Avatar assignment/removal also consumes 20 avatar changes/hour/user.

**Important errors:** profile validation errors, Media authorization/lifecycle errors such as `MEDIA_ACCESS_DENIED`/`MEDIA_NOT_FOUND`, account-state Auth errors, rate-limit errors, and `INTERNAL_ERROR`.

### GET `/api/method/aos.api.v1.accounts.get_my_preference`

**Authentication:** required active/enabled account.

**Purpose / frontend usage:** read the authenticated user's persisted browsing/localization preference for settings and market controls.

**Request fields:** none. Unknown fields return `PREFERENCE_UNKNOWN_FIELD`.

**Response:** exactly:

```json
{
  "country": "Kenya",
  "currency": "KES",
  "language": "en",
  "location": "LOC-..."
}
```

`location` is `null` when no location is selected.

**Rate limit:** 60/minute/user.

**Side effects / transaction / retry:** read-only, private/no-store, no row creation or repair, retry-safe. Missing required state returns `PREFERENCE_MISSING`.

### POST `/api/method/aos.api.v1.accounts.update_my_preference`

**Authentication:** required active/enabled account.

**Purpose / frontend usage:** update the current browsing/buyer localization preference from account/settings/market controls.

**Accepted fields:** `country`, `currency`, `language`, `location`. This is a strict partial update and requires at least one field. Unknown fields return `PREFERENCE_UNKNOWN_FIELD`.

**Validation and ownership:** Accounts delegates values to Localization. Localization validates Country/Currency/Language master data and AOS Location activity/country ownership. Accounts does not ask whether the user is a seller, owns a storefront, has active ads, or has ever posted an ad.

**Country/location invariant:** country is freely mutable. When country changes and the existing `location` belongs to another country, Localization clears location to `null`. A supplied location must belong to the resulting country; sending `location: null` explicitly clears the current location. Currency and language remain unchanged unless explicitly provided.

**Transaction/concurrency:** Localization selects the single preference row `FOR UPDATE`; concurrent updates serialize at the database. It validates the complete next state before saving and does not commit manually. Cache invalidation occurs immediately and after commit through the shared Frappe cache so another app node cannot retain a stale committed preference after a refill race.

**Idempotency:** sending values already persisted is a no-op and does not issue an unnecessary document save. Retries do not create rows or duplicates.

**Rate limit:** 20/minute/user.

**Important errors:** `PREFERENCE_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `PREFERENCE_MISSING`, `INVALID_COUNTRY`, `INVALID_CURRENCY`, `DISABLED_CURRENCY`, `INVALID_LANGUAGE`, `DISABLED_LANGUAGE`, `INVALID_LOCATION`, rate-limit errors, and `INTERNAL_ERROR`.

## Business rules

A user's country preference is the current mutable browsing/buyer market. It is not the market ownership record for listings. A seller is also a buyer and may switch country at any time.

An Ad owns its persisted `country`, `location`, and `currency` after creation. New Ad creation snapshots the user's current preference into the new Ad and validates the selected location against that market. Later account-preference changes never rewrite existing Ads. Editing/resubmitting an existing Ad validates locations against that Ad's persisted country rather than the seller's current browsing country. A user can therefore browse Uganda while an existing Kenya/Nairobi Ad remains unchanged, and can maintain listings in multiple markets over time.

Currency and language preferences are independently mutable and are not automatically forced when country changes. Localization's current validation/master-data rules are authoritative.

## Authorization, privacy, and security

- Every Accounts endpoint is session-authenticated and runs the canonical account-state guard. Deleted, suspended, disabled, and Guest identities cannot use normal authenticated Accounts mutations.
- Cross-user profile mutation is impossible by contract because update ownership comes only from the session.
- Public lookup accepts only `ACC-*`, preventing email/internal-identity aliases from becoming a public enumeration surface.
- Profile mutation is allowlist-based. Unknown and server-derived fields fail closed, preventing mass assignment and privilege/seller/verification escalation.
- Public serialization is an allowlist; adding a DocType field does not expose it automatically.
- Media ownership/purpose/attachment authorization is delegated to Media before Profile can reference an object.
- User text is size-bounded and normalized; control characters/null bytes are rejected. Clients must render user text as text rather than trusted HTML.
- Unexpected exceptions are logged server-side and become safe `INTERNAL_ERROR` responses; stack traces, SQL, filesystem paths, secrets, and raw internal identities are not returned.
- Rate limits are shared across nodes through Redis/Frappe cache; no process-local counter or lock protects correctness.

## Transactions and concurrency

Profile mutations lock the unique Profile row with `SELECT ... FOR UPDATE`. Preference mutations lock the unique User Preference row similarly. These locks are database-scoped and work with multiple Frappe application nodes. The unique `AOS Profile.user` and `AOS User Preference.user` constraints are the final duplicate-creation barriers.

Authentication bootstrap treats duplicate profile/preference inserts as a normal race: after a unique-key collision, the losing node resolves and returns the row created by the winner. Reads never perform bootstrap repair.

Media/Profile/User changes participate in one request transaction. Preference validation computes the complete next country/currency/language/location state under lock before saving. Accounts mutation endpoints do not perform manual success commits.

## Scalability and high availability

Accounts hot paths use bounded projections rather than loading arbitrary document graphs. Public/self Profile lookup resolves a single Profile+User row through indexed primary/unique keys. Seller, verification, Social relationship, and Media presentation data are bounded one-account projections. The internal identity serializer supports batch lookup to avoid N+1 identity queries in cross-feature lists.

Preference reads use a short-lived shared Redis cache keyed by a SHA-256 digest of the internal identity. The database remains the source of truth. Cache failure degrades to the database. Mutation invalidation runs immediately and after commit to reduce stale-refill races across nodes.

Accounts exposes no list/search endpoint, so there is no unbounded profile enumeration or deep offset-pagination path in this domain. Any future account search must be independently bounded and indexed.

Lifecycle/purge scans use dedicated composite indexes. The public account primary key and internal user link are indexed by primary/unique constraints. Additional speculative indexes are intentionally avoided unless a current query path requires them.

## Cross-feature integrations

### Authentication

Authentication supplies session identity, account-state rules, bootstrap creation, canonical `/me`, login/logout, credentials, 2FA, email verification, and delete/restore authentication flows. Auth bootstrap preferences use the same four fields as Accounts: `country`, `currency`, `language`, `location`.

### Localization

Localization owns master data, default selection, guest/authenticated context resolution, preference persistence semantics, country/location consistency, validation, row locking, and preference-cache invalidation. Accounts delegates rather than reproducing those rules.

### Media

Media owns upload, storage, purpose policy, ownership, lifecycle, attachment references, replacement/release semantics, and URL generation. Accounts attaches only Media authorized for the current Profile's `profile_image` field.

### Ads

Ads owns the market context persisted on each Ad. Account/Localization preference updates have no side effect on Ads. Existing Ad location validation uses the Ad's persisted country.

### Sellers and Verification

Accounts reads bounded seller/verification display projections. Sellers and Verification remain authoritative for lifecycle/state changes. Accounts profile mutation cannot set either state.

### Social

Social owns follow/block relationships and friend counts. Accounts uses Social's relationship projection to decide whether a target public profile is available and to attach viewer-relative relationship fields; it does not duplicate Social mutation logic.

## Frontend contract

Web/mobile clients should rely on these current contracts:

- use `account_id` (`ACC-*`) for public account references;
- never route by Frappe User email as an account identifier;
- treat owner `email` only as owner-visible Auth/account data;
- treat public profile and self profile as different privacy projections;
- send only supported fields to `update_profile`;
- upload profile media through Media first, then send its Media ID as `avatar_media_id`;
- treat preferences as exactly `country`, `currency`, `language`, `location`;
- allow country changes for buyers and sellers alike;
- expect `location` to become `null` when the selected country makes the previous location incompatible;
- never infer that changing browsing country moves or changes an existing Ad;
- branch on stable `error` codes rather than message text.
