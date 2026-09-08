# AOS Authentication

<!-- BEGIN CODE-DERIVED ENDPOINTS -->
## Endpoint inventory (code-derived)

This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.

| Endpoint | HTTP | Decorator access | Audience |
|---|---|---|---|
| `apple_login` | POST | Guest allowed | Client |
| `change_password` | POST | Session required | Client |
| `delete_account` | POST | Session required | Client |
| `forgot_password_request` | POST | Guest allowed | Client |
| `forgot_password_reset` | POST | Guest allowed | Client |
| `forgot_password_verify_otp` | POST | Guest allowed | Client |
| `google_login` | POST | Guest allowed | Client |
| `login` | POST | Guest allowed | Client |
| `logout` | POST | Guest allowed | Client |
| `me` | GET | Guest allowed | Client |
| `register` | POST | Guest allowed | Client |
| `request_restore_account` | POST | Guest allowed | Client |
| `resend_email_otp` | POST | Guest allowed | Client |
| `restore_account` | POST | Guest allowed | Client |
| `verify_email_otp` | POST | Guest allowed | Client |
| `verify_two_factor` | POST | Guest allowed | Client |

`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.
<!-- END CODE-DERIVED ENDPOINTS -->

This is the single authoritative feature document for AOS Authentication. The public backend contract is the versioned `aos.api.v1.auth.*` namespace. Modules under `aos.api.auth.*` are implementation details and are not frontend contracts.

## A. Overview

Authentication establishes and recovers AOS user identity and creates/invalidates Frappe sessions. It owns:

- email/password registration;
- email verification OTPs;
- email/password login;
- Google and Apple OIDC login;
- current-user/session bootstrap (`me`);
- idempotent logout;
- password recovery and authenticated password change;
- recoverable account deletion confirmation and account restoration verification;
- Authentication-specific abuse limits and safe security logging;
- durable provider-subject bindings for social identity.

Authentication does **not** own authorization policy for marketplace features, seller business rules, Localization rules, account-lifecycle cleanup policy, or Frappe's password hashing/session engine. Those are consumed through their existing service/framework boundaries.

### Supported authentication methods

| Method | Public identifier | Proof | Session result |
|---|---|---|---|
| Password | normalized email in `identifier` | Frappe password verifier | web cookie or mobile `sid` |
| Google | Google OIDC ID token | RS256/JWKS + issuer/audience/expiry + verified email | web cookie or mobile `sid` |
| Apple | Apple OIDC identity token | RS256/JWKS + issuer/audience/expiry | web cookie or mobile `sid` |

Phone login and username login are not supported by the AOS Authentication contract.

### Major flows

```text
Email signup
  -> validate + normalize
  -> create disabled Frappe User
  -> create AOS Profile
  -> initialize AOS User Preference through Localization
  -> create verification state + queue OTP email
  -> verify OTP
  -> enable User

Password login
  -> validate exact contract
  -> shared Redis abuse limits
  -> resolve exact normalized email
  -> account-scoped DB row lock
  -> Frappe credential verification
  -> account-state + bootstrap invariant checks
  -> Frappe session creation
  -> safe bootstrap serialization

Social login
  -> validate exact contract
  -> shared Redis abuse limits
  -> provider OIDC/JWKS verification
  -> resolve immutable provider `sub`
  -> resolve/create User and durable AOS Auth Identity binding
  -> account-state + bootstrap invariant checks
  -> Frappe session creation

Password recovery
  -> generic recovery request
  -> queue OTP for eligible account
  -> verify OTP
  -> issue high-entropy one-time reset token
  -> verify token + update Frappe password
  -> consume token + revoke all sessions
```

### Relationship with Frappe

AOS delegates password hashing/verification and session mechanics to Frappe. AOS adds the public request contract, enumeration protection, account state policy, distributed abuse limits, AOS bootstrap checks, OIDC identity binding, safe responses, and application-level concurrency ordering.

Frappe also applies a coarse site-wide User-creation throttle in its `User.before_insert` path. That global guard is retained for generic Frappe/Desk User creation, but it is too coarse to be the public AOS signup limiter because unrelated users and application nodes share the same site-wide count. The `User` controller is extended with `AOSAuthUserMixin`; only Website User documents carrying a trusted in-process AOS sentinel bypass that one framework guard, and only while the upstream `before_insert` hook runs. Email/password registration and first-time social login set the sentinel only after their shared Redis abuse checks. The sentinel is an object-identity value and cannot be supplied through a request or serialized DocType field.

Frappe's generic login path is intentionally blocked for AOS `Website User` accounts by the `on_login` hook. System Users and Administrator retain normal Frappe/Desk authentication. This prevents AOS Website Users from bypassing the versioned AOS login controls.

### Relationship with Localization

Localization remains authoritative for country, language, currency, normalization, enabled master data, defaults and preference serialization. Authentication calls Localization only when creating a new account. Existing login and `me` never synthesize or repair Localization state.

### Relationship with authorization

Authentication returns roles and seller summary as server-derived bootstrap information. Clients cannot submit roles, seller status, verification state, account status, or internal user IDs to elevate privilege. Feature authorization remains server-side in the owning services.

---

## B. Architecture

```text
Web / Mobile
    |
    v
`aos.api.v1.auth.*` thin whitelisted wrappers
    |
    v
Auth endpoint implementation (`aos.api.auth.*`)
    |-- contracts.py       exact-key enforcement
    |-- validators.py      normalization + primitive validation
    |-- rate_limits.py     shared Redis abuse counters
    |-- locking.py         per-account DB row ordering
    |
    +--> password / OTP / OIDC orchestration
    |       |
    |       +--> Frappe password + session primitives
    |       +--> OIDC JWKS / PyJWT
    |
    +--> account_helpers.py
    |       +--> Accounts profile bootstrap (new users only)
    |       +--> Localization preference bootstrap (new users only)
    |
    +--> AOS repositories / DocTypes
    |       +--> User
    |       +--> AOS Profile
    |       +--> AOS User Preference
    |       +--> AOS Auth Challenge
    |       +--> AOS Auth Identity
    |       +--> Sessions
    |
    v
serializers.py -> explicit public response
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `aos/api/v1/auth/__init__.py` | thin public v1 wrappers and HTTP/guest decorators |
| `session.py` | password login, `me`, idempotent logout |
| `register.py` | atomic email/password account creation |
| `otp.py` | email verification and resend orchestration |
| `otp_service.py` | OTP issuance/verification policy |
| `verification.py` | secure OTP/token primitives and verification row repository helpers |
| `password_reset.py` | enumeration-safe recovery, reset-token consumption, session revocation |
| `password_change.py` | authenticated current-password proof and password replacement |
| `passwords.py` | shared Frappe password-policy/reuse/write boundary |
| `google_login.py` / `apple_login.py` | provider-specific endpoint contracts/settings |
| `social_login.py` | common OIDC account/session orchestration |
| `oidc.py` | shared PyJWT/JWKS verification and dependency classification |
| `social_identity.py` | immutable provider-subject binding repository |
| `account_helpers.py` | new-user AOS Profile + Localization bootstrap plus read-only bootstrap invariant/preference loading |
| `session_control.py` | exact server-session/token revocation without wildcard cache deletion |
| `session_hooks.py` | prevents generic Frappe Website User login bypass |
| `framework_guards.py` | closes parallel Frappe Website signup/recovery/password endpoints while preserving System User Desk/admin recovery and password administration |
| `user_controller.py` | extends the Frappe `User` controller so only internally marked, already-rate-limited AOS Website User creation bypasses Frappe's coarse site-wide creation throttle; generic User creation remains framework-throttled |
| `rate_limits.py` | atomic Redis INCR+EXPIRE via Lua, site-keyed HMAC non-IP key dimensions, fail-closed dependency behavior |
| `locking.py` | account-scoped `User` row lock used by security mutations/session creation |
| `serializers.py` | allowlisted user/session/preferences/roles/seller response shape; optional avatar resolution degrades safely |
| `contracts.py` | rejects deprecated aliases and unknown request keys |
| `validators.py` | strict strings, email normalization, client type and password input bounds plus Frappe configured strength-policy delegation |
| `aos/install.py` | fresh-site default disabling Frappe public signup |

---

## C. Data model

```text
Frappe User
 |-- 1:1 AOS Profile
 |-- 1:1 AOS User Preference  -> Localization masters/rules
 |-- 0..4 AOS Auth Challenge (one deterministic row per purpose)
 |-- 0..2 AOS Auth Identity (at most one per supported provider)
 `-- 0..N Frappe Sessions
```

### Frappe `User`

Purpose: framework-owned identity, password hash, enabled state and Website/System user classification.

Authentication uses the framework document and password APIs; plaintext passwords are never stored by AOS.

Meaningful fields used by Authentication:

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `name` | framework primary key | yes | primary | internal User identity |
| `email` | Data | yes for AOS Website User | framework-managed | canonical AOS password-login identifier |
| `enabled` | Check | yes | framework-managed | blocks session establishment when disabled |
| `user_type` | Select | yes | framework-managed | AOS users are `Website User`; Desk users remain framework-managed |
| password hash | framework auth storage | yes for password accounts | framework-managed | secure password verification |

Lifecycle: created disabled for password signup, enabled after email verification; social-created users are enabled after verified provider identity. Account deletion/restoration is owned by Accounts lifecycle and Auth consumes the resulting state. AOS account email is immutable in the current contract; there is no partial rename/email-change endpoint.

### `AOS Profile`

Purpose: AOS account/profile state required by all marketplace identity surfaces.

Authentication relies on the existing profile contract. The profile primary key itself is the immutable opaque `ACC-*` account id and `user` is a unique Link to Frappe User. `account_status` and `restore_deadline` define the reversible deletion boundary; deletion is derived from `account_status == Deleted` rather than stored twice. Composite lifecycle/purge indexes support the background permanent-deletion scan.

Authentication-relevant fields:

| Field | Type / constraint | Purpose |
|---|---|---|
| `name` | primary `ACC-*` id | immutable opaque public account id; there is no separate `public_id` field |
| `user` | Link User, required, unique | 1:1 link to Frappe authentication identity |
| `display_name` | Data, required | allowlisted login/`me` display name |
| `profile_image_media` | Link AOS Media Object, indexed | optional avatar reference; resolution failure degrades to `avatar: null` |
| `account_status` | Select `Active/Deleted/Suspended` | product lifecycle gate consumed before session bootstrap |
| `is_verified` | read-only Check | hot-read projection returned by Authentication; verification workflow remains Verification-owned |
| `deleted_at` | Datetime | time reversible deletion started |
| `restore_deadline` | Datetime, indexed | last time account can be restored; normally deletion + 30 days |
| `purge_status` | Select, indexed | `Pending/Purging/Completed` durable permanent-cleanup progress |
| `purge_started_at` | Datetime | first bounded cleanup run after restore expiry |
| `purge_completed_at` | Datetime | permanent private-data cleanup/anonymization completion |
| `restored_at` | Datetime | latest successful restore time |

Profile fields such as bio, phone, date of birth and gender are AOS-owned and are not duplicated into Frappe User. Display name and avatar may be projected into Frappe User solely for framework/admin presentation. Localization is the only owner of account location. Verification Request owns verification audit metadata; `AOS Profile.is_verified` is only a deliberate hot-read projection.

Authentication does not repair a missing profile during login or `me`; a missing row is an internal account-bootstrap invariant failure (`ACCOUNT_BOOTSTRAP_UNAVAILABLE`).

### `AOS User Preference`

Purpose: user-owned Localization selection.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `user` | Link User | yes | unique / deterministic name | one preference per AOS user |
| `country` | Link/master value | yes | schema-defined | Localization country |
| `language` | Link/master value | yes | schema-defined | Localization language |
| `currency` | Link/master value | yes | schema-defined | Localization currency |
| `location` | Link `AOS Location` | no | linked lookup | optional market location owned/validated by Localization |

Creation/default resolution belongs to Localization. Authentication initializes this row only while creating a new user. The DocType uses `field:user` naming and a unique `user` field; the Localization final-state patch defensively verifies the DB uniqueness constraint. Missing preference on an existing account returns the single public invariant error `ACCOUNT_BOOTSTRAP_UNAVAILABLE` and is not silently recreated on a high-frequency request. Preference cache is shared Redis with DB fallback; Authentication does not own its normalization rules.

### `AOS Auth Challenge`

Purpose: bounded temporary state for email verification, password recovery, account restoration, and two-factor login continuation. There is one deterministic document name per `User + purpose`; repeated sends overwrite the same row rather than creating unbounded OTP history.

Valid purposes: `email_verification`, `password_reset`, `account_restore`, `two_factor`.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `user` | Link User | yes | search index + input to deterministic primary-name hash | owner of security state and efficient account-wide revocation |
| `purpose` | Select | yes | input to deterministic primary-name hash | isolates verification flows |
| `otp_password_hash` | Data, hidden/read-only | no | no | slow Frappe password hash of current OTP; never stores OTP plaintext |
| `expires_at` | Datetime | no | no | OTP expiry |
| `last_sent_at` | Datetime | no | no | internal resend cooldown |
| `attempts` | Int | no | no | bounded OTP guesses for current code |
| `continuation_token_hash` | Data(64), hidden/read-only | no | search index | SHA-256 digest of a high-entropy continuation token used by password reset and 2FA |
| `continuation_expires_at` | Datetime | no | no | continuation-token expiry |
| `is_used` | Check | no | no | one-time OTP consumption marker |

Invariants:

- document name is `authc-<sha256(user + NUL + purpose)>` and renaming is disabled; the fixed-length key avoids Frappe name-length issues for long email addresses;
- OTP code is generated with `secrets` and slow-hashed with Frappe's password hashing context;
- continuation tokens are high entropy, stored only as digests and compared in constant time;
- OTP attempts are serialized by row lock;
- successful consumption clears the OTP verifier;
- password reset clears continuation-token state and revokes sessions;
- System Manager has read/report-only Desk access; Authentication service code owns mutation via controlled server-side paths;
- rows are bounded to at most one per user/purpose and are reused rather than append-only; account access revocation clears/consumes Authentication challenge state, while permanent account cleanup may delete it under Accounts ownership.

### `AOS Auth Identity`

Purpose: durable social identity mapping using the immutable OIDC provider subject instead of mutable/reassignable email, without persisting the raw subject.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `provider` | Select (`google`, `apple`) | yes | standard filter | identity provider |
| `user` | Link User | yes | search index | bound AOS user |
| `user_provider_key` | Data, hidden/read-only | yes | unique | prevents one AOS user from silently binding multiple subjects for the same provider |

The primary document name is a site-keyed HMAC-derived identifier from provider + subject; the raw provider subject is never persisted. `user_provider_key` is also site-keyed and unique. A conflicting identity is never silently rebound during login. System Manager has read/report-only Desk access; Authentication service code owns creation/mutation. Bindings persist across the recoverable deletion window so an account can be restored safely; Accounts permanent purge owns eventual binding deletion.

### Frappe `Sessions`

Framework-owned session persistence. AOS queries `sid` values only for exact user-scoped revocation. Database deletion is done inside the surrounding transaction; exact Redis `session` hash entries are invalidated after commit. Wildcard Redis key deletion is prohibited.

---

## D. Endpoint reference

All public Authentication endpoints use `/api/method/aos.api.v1.auth.<method>`. The versioned wrapper is the only client contract; `aos.api.auth.*` modules are internal implementation.

Standard AOS success envelope:

```json
{"ok":true,"message":"...","data":{}}
```

Standard AOS failure envelope:

```json
{"ok":false,"message":"...","error":"STABLE_CODE","data":{}}
```

`error` is the only machine-readable failure key. Expected errors receive the HTTP status mapped by `aos.api.shared.responses`; unexpected failures are secret-safely logged and return `SERVICE_UNAVAILABLE` rather than raw Frappe, SQL, Redis, OIDC or Python exception text. Frappe RPC transport injects `cmd`; the v1 wrapper strips only that transport key. Every other unknown client field is rejected with `AUTH_UNKNOWN_FIELD`.

### Canonical authenticated bootstrap payload

Successful password/social/2FA login uses this shape. `sid` appears only when `client_type = "mobile"`.

```json
{
  "session": {"authenticated": true, "sid": "<mobile-only>"},
  "user": {
    "account_id": "ACC-EXAMPLEOPAQUEID",
    "email": "jane@example.com",
    "display_name": "Jane Doe",
    "avatar": null,
    "enabled": true,
    "account_status": "Active",
    "is_verified": false
  },
  "preferences": {
    "country": "Kenya",
    "currency": "KES",
    "language": "en",
    "location": null,
    "is_country_locked": false
  },
  "roles": ["Website User"],
  "seller": {"is_seller": false, "seller_id": null, "status": null}
}
```

For a seller, `seller` additionally contains server-derived `seller_type`, `business_category`, `rating`, and `total_reviews`; `is_seller` reflects active seller state. `roles`, account status, verification state and seller state are never client-supplied. Optional avatar/media URL resolution is degradable: if that presentation dependency fails, `avatar` is `null` and Authentication can still succeed.

### `aos.api.v1.auth.register`

**Purpose:** create an email/password AOS account and queue signup verification.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** registration screen before email verification.

| Parameter | Type | Required | Default | Normalization / validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim, lowercase, valid email |
| `password` | string, 8..128 | yes | — | no trim; AOS baseline + configured Frappe strength policy |
| `full_name` | string, max 140 | yes | — | trim, collapse whitespace, minimum normalized length 2 |
| `country` | string, max 140 | no | Localization resolver | validated/resolved by Localization |
| `currency` | string, max 32 | no | Localization resolver | validated/resolved by Localization |
| `language` | string, max 140 | no | Localization resolver | validated/resolved by Localization |

Example request:

```json
{"email":"jane@example.com","password":"StrongPass123!","full_name":"Jane Doe","country":"Kenya","currency":"KES","language":"en"}
```

Success: `200` with `data: {}` and message `If this email can be registered, a verification code has been queued.` The same response is used for a normalized email that already exists and for a concurrent duplicate insert.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, Localization validation/dependency errors for supplied/default hints, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`, `REGISTER_FAILED`. Existing-account state is not disclosed.

Side effects: after validation/rate limiting, inserts a disabled Frappe Website User, AOS Profile, Localization-owned AOS User Preference, deterministic email-verification AOS Auth Challenge and Frappe Email Queue state. These request-owned writes remain in the surrounding request transaction; queue/bootstrap failure rolls back the new account. The managed User sentinel bypasses only Frappe's coarse site-wide User-creation throttle after AOS distributed limits pass.

Idempotency/retry: existing-address retries are safely acknowledged without mutation or re-send. DB uniqueness is the final race guard. A valid request for a genuinely new address is intentionally account-creating.

Rate limiting: 5/hour per normalized email + 120/hour per IP. Non-IP Redis key material is digested.

### `aos.api.v1.auth.verify_email_otp`

**Purpose:** consume signup email proof and activate the account.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** signup verification screen.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |
| `otp` | string | yes | — | exactly six decimal digits; general input cap 12 |

Example: `{"email":"jane@example.com","otp":"123456"}`

Success: `200`, `data: {}`, message `Email verified. Account activated.`

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `OTP_INVALID`, `RATE_LIMIT`, and public-safe availability handling. Missing, wrong, expired, exhausted and replayed challenge states collapse to `OTP_INVALID` at this public boundary.

Side effects: locks User then challenge row, consumes OTP and enables the Frappe User in the request transaction.

Idempotency/retry: successful OTP is single-use; replay is stable `OTP_INVALID`, not another activation.

Rate limiting: 30/hour per normalized email + 120/hour per IP.

### `aos.api.v1.auth.resend_email_otp`

**Purpose:** queue a replacement signup verification code when verification is still needed.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** signup verification “resend code”.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |

Example: `{"email":"jane@example.com"}`

Success: `200`, `data: {}`, message `If verification is required for this email, a code has been queued.` Unknown email, already-active account, 60-second cooldown suppression and actual queued resend intentionally share this response.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `RATE_LIMIT`; unexpected infrastructure failures are internally logged while the enumeration-safe request acknowledgement is retained when possible.

Side effects: only an eligible account outside cooldown gets new OTP state + Email Queue state. No-op branches perform comparable slow OTP-hash work to reduce simple timing/cooldown enumeration.

Idempotency/retry: safe within limits; cooldown prevents mail flooding without exposing whether it fired.

Rate limiting: 10/hour per normalized email + 60/hour per IP; internal resend cooldown is 60 seconds.

### `aos.api.v1.auth.login`

**Purpose:** canonical password login.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** email/password login screen.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `identifier` | string, max 140 | yes | — | **email only**, trim/lowercase, valid email |
| `password` | string, max 128 | yes | — | non-empty, no trim, Frappe credential verifier |
| `client_type` | string, max 16 | yes | — | `web` or `mobile` |

Example: `{"identifier":"jane@example.com","password":"StrongPass123!","client_type":"mobile"}`

Success: `200`, message `Login successful.`, with the canonical authenticated bootstrap payload above. Mobile includes `data.session.sid`; web omits it and uses the Frappe HttpOnly session cookie.

Stable errors include `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `INVALID_CREDENTIALS`, `EMAIL_NOT_VERIFIED`, `ACCOUNT_DISABLED`, `ACCOUNT_SUSPENDED`, `ACCOUNT_DELETED`, `ACCOUNT_DELETED_RESTORABLE`, `ACCOUNT_BOOTSTRAP_UNAVAILABLE`, `PASSWORD_RESET_REQUIRED`, `TWO_FACTOR_REQUIRED`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`.

Unknown user and wrong password are exactly `INVALID_CREDENTIALS` / `Invalid credentials.`. Unknown-user flow performs slow dummy password verification. Disabled/deleted/pending lifecycle distinctions are returned only after valid password proof.

Side effects: Frappe credential checks; if no 2FA continuation is required, Frappe session creation and normal framework login metadata. Existing Profile/Preference state is read-only and is never repaired here. Bootstrap DB reads are completed before session creation; optional avatar resolution can degrade to `null`.

Idempotency/retry: session creation is intentionally non-idempotent; a blind retry can produce another valid Frappe session according to site simultaneous-session settings.

Rate limiting: 20/hour per normalized identifier + 300/hour per IP; Frappe's enabled-user login-attempt tracker remains in the credential path.

### `aos.api.v1.auth.verify_two_factor`

**Purpose:** complete password/Google/Apple login when Frappe policy requires AOS email OTP as a second factor.  
**HTTP / authentication:** `POST`, Guest continuation.  
**Frontend usage:** login 2FA screen shown after `TWO_FACTOR_REQUIRED`.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `challenge_token` | string, max 4096 | yes | — | high-entropy opaque token issued after first factor |
| `otp` | string | yes | — | exactly six digits |
| `client_type` | string, max 16 | yes | — | `web` or `mobile` |

Example: `{"challenge_token":"<opaque>","otp":"123456","client_type":"mobile"}`

First-factor continuation response: HTTP `403`, error `TWO_FACTOR_REQUIRED`, `data = {"challenge_token":"<opaque>","method":"email","expires_in_seconds":900}`. Only its digest is stored.

Success: `200`, `Login successful.`, canonical authenticated bootstrap payload. Mobile includes `sid`; web does not.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `TOKEN_INVALID`, `OTP_INVALID`, account-state errors, `ACCOUNT_BOOTSTRAP_UNAVAILABLE`, `PASSWORD_RESET_REQUIRED`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`.

Side effects: User row then challenge row are locked; OTP + continuation state are consumed before Frappe session creation. Repeated first-factor success inside the 60-second resend cooldown rotates the continuation token but reuses the still-valid OTP instead of queuing another email; used/expired/missing OTP state causes a fresh OTP.

Idempotency/retry: successful challenge is single-use; replay is `TOKEN_INVALID`. Requesting a new continuation does not make email delivery unbounded inside cooldown.

Rate limiting: 10/hour per challenge-token digest + 120/hour per IP.

### `aos.api.v1.auth.google_login`

**Purpose:** verify Google OIDC identity, bind/create the AOS account, then create an AOS/Frappe session.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** Google sign-in button/callback.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `id_token` | string, max 4096 | yes | — | RS256 Google ID token |
| `client_type` | string, max 16 | yes | — | `web` or `mobile` |
| `country` | string, max 140 | no | Localization resolver | used only when a new AOS account must be created |
| `currency` | string, max 32 | no | Localization resolver | new-account hint only |
| `language` | string, max 140 | no | Localization resolver | new-account hint only |

Example: `{"id_token":"<google-id-token>","client_type":"web","country":"Kenya"}`

Success: `200`, `Login successful.`, canonical authenticated bootstrap payload; may instead return `TWO_FACTOR_REQUIRED` before session creation.

Stable errors include `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `CONFIG_ERROR`, `TOKEN_INVALID`, `TOKEN_EXPIRED`, `EMAIL_NOT_VERIFIED`, `SOCIAL_IDENTITY_CONFLICT`, account/bootstrap errors, `TWO_FACTOR_REQUIRED`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`.

Security/side effects: verifies RS256 signature, Google issuer/audience/expiry/required claims and verified email. Immutable provider `sub` is the durable identity. A first social sign-in may atomically create User/Profile/Preference + opaque AOS Auth Identity; an existing provider binding is authoritative and is never silently rebound by email. Session creation occurs only after account/bootstrap checks.

Idempotency/retry: existing bound identity is reuse-safe; first-account creation relies on DB uniqueness and identity-binding constraints. Session creation itself is non-idempotent.

Rate limiting: 300/hour per IP before token verification + 30/hour per verified provider subject after token proof.

### `aos.api.v1.auth.apple_login`

**Purpose:** verify Apple OIDC identity, bind/create the AOS account, then create an AOS/Frappe session.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** Sign in with Apple callback.

Inputs and size limits are identical to `google_login`: `id_token` and `client_type` required; `country`, `currency`, `language` optional new-account hints.

Example: `{"id_token":"<apple-identity-token>","client_type":"mobile"}`

Success/session/error/idempotency behavior matches Google, with Apple-specific token verification. First sign-in requires provider email when no subject binding exists, and any supplied Apple email must be provider-verified. Later Apple tokens may omit email because the existing opaque `sub` binding resolves the account.

Rate limiting: 300/hour per IP + 30/hour per verified Apple subject.

### `aos.api.v1.auth.me`

**Purpose:** high-frequency authenticated bootstrap on app startup/resume/session recovery.  
**HTTP / authentication:** `GET`; decorator permits Guest only so AOS can return stable JSON, but a valid session is logically required.  
**Frontend usage:** validate persisted auth state and refresh current account/bootstrap state.

Inputs: **none**. Any client field is `AUTH_UNKNOWN_FIELD`.

Example: request with valid Frappe web cookie or mobile session transport and no Authentication parameters.

Success: `200`, message `Session fetched.`, canonical bootstrap payload with `data.session = {"authenticated":true}`. `sid` is **never** returned by `me`.

Stable errors: `AUTH_UNKNOWN_FIELD`, `SESSION_INVALID`, account-state errors, `ACCOUNT_BOOTSTRAP_UNAVAILABLE`, `SERVICE_UNAVAILABLE`.

Side effects: no AOS repair/create writes. Frappe may perform normal session bookkeeping. Required Preference is loaded once and reused for serialization; optional avatar failure degrades to `null`.

Idempotency/retry: yes, read-oriented and safe to retry.

Rate limiting: no additional Auth Redis quota; normal Frappe/edge/session controls still apply.

### `aos.api.v1.auth.logout`

**Purpose:** terminate the current Frappe session.  
**HTTP / authentication:** `POST`, Guest allowed for idempotency.  
**Frontend usage:** logout action and best-effort local session cleanup.

Inputs: **none**.

Success when authenticated: `200`, `Logged out successfully.`; when already Guest/expired: `200`, `Already logged out.` Both use `data: {}`.

Stable errors: `AUTH_UNKNOWN_FIELD`, `LOGOUT_FAILED` if framework logout unexpectedly fails.

Side effects: current Frappe session/cookie teardown when authenticated.

Idempotency/retry: yes; repeated logout reaches the same logged-out state.

Rate limiting: no additional Authentication Redis quota.

### `aos.api.v1.auth.forgot_password_request`

**Purpose:** request password-recovery OTP without revealing whether an eligible account exists.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** “forgot password” first step.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |

Example: `{"email":"jane@example.com"}`

Success: `200`, `data: {}`, message `If an account exists for this email, a recovery code has been queued.` Unknown/deleted/eligible/cooldown-suppressed states share it.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE` when the distributed limiter itself is unavailable. Post-limit account/mail failures are logged but do not become an account-enumerating response.

Side effects: eligible account outside cooldown gets password-reset OTP + Email Queue state; challenge continuation is cleared before a fresh OTP. Unknown/deleted/cooldown no-op branches do slow dummy OTP-hash work.

Idempotency/retry: safe within limits; 60-second internal resend cooldown can suppress duplicate delivery.

Rate limiting: 10/hour per normalized email + 60/hour per IP.

### `aos.api.v1.auth.forgot_password_verify_otp`

**Purpose:** exchange valid recovery OTP for a one-time reset token.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** recovery OTP verification step.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |
| `otp` | string | yes | — | exactly six digits |

Example: `{"email":"jane@example.com","otp":"123456"}`

Success: `200`, message `OTP verified.`, `data = {"reset_token":"<one-time-high-entropy-token>"}`. Plaintext reset token is returned only to this successful caller; server stores only its digest.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `OTP_INVALID`, `RATE_LIMIT`; unexpected failures collapse to the generic public OTP failure.

Side effects: consumes OTP and writes reset-token digest + 15-minute expiry under User/challenge locks.

Idempotency/retry: OTP is single-use; replay is `OTP_INVALID`.

Rate limiting: 30/hour per normalized email + 180/hour per IP.

### `aos.api.v1.auth.forgot_password_reset`

**Purpose:** consume reset token, replace password and revoke all sessions.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** recovery “set new password” screen.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |
| `reset_token` | string, max 4096 | yes | — | opaque one-time token |
| `new_password` | string, max 128 | yes | — | after token proof: min 8 + Frappe policy + no current-password reuse |
| `confirm_password` | string, max 128 | yes | — | must exactly equal `new_password` |

Example: `{"email":"jane@example.com","reset_token":"<opaque>","new_password":"NewStrongPass123!","confirm_password":"NewStrongPass123!"}`

Success: `200`, `data: {}`, message `Password updated successfully. Please login again.`

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `PASSWORD_MISMATCH`, `TOKEN_INVALID`, `TOKEN_EXPIRED`, `PASSWORD_REUSED`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`, `INTERNAL_ERROR`. Password policy/reuse details are evaluated only after valid reset-token proof.

Side effects: under User then challenge lock, writes Frappe password, consumes reset continuation and deletes all Frappe sessions in the same DB transaction; exact Redis session entries are invalidated after commit.

Idempotency/retry: reset token is single-use; post-success replay is `TOKEN_INVALID`.

Rate limiting: 20/hour per normalized email + 120/hour per IP.

### `aos.api.v1.auth.change_password`

**Purpose:** authenticated password replacement with current-password proof.  
**HTTP / authentication:** `POST`, authenticated Frappe session.  
**Frontend usage:** Account/Security “change password”.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `current_password` | string, max 128 | yes | — | verified by Frappe |
| `new_password` | string, max 128 | yes | — | min 8 + Frappe policy + not current password |
| `confirm_password` | string, max 128 | yes | — | exact match |

Example: `{"current_password":"OldStrong123!","new_password":"NewStrong456!","confirm_password":"NewStrong456!"}`

Success: `200`, `data: {}`, `Password changed successfully.`

Stable errors: `AUTH_UNKNOWN_FIELD`, `AUTH_REQUIRED`/session auth error from shared auth boundary, `VALIDATION_ERROR`, `PASSWORD_MISMATCH`, `PASSWORD_REUSED`, `FORBIDDEN` for wrong current password, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`, `INTERNAL_ERROR`.

Side effects: User row lock, password update, retain current SID and revoke all other exact server sessions; exact Redis session invalidation is after commit.

Idempotency/retry: intentionally non-idempotent credential mutation. A repeated request with the old current password fails after the first successful change.

Rate limiting: 20/hour per authenticated user + 30/hour per IP.

### `aos.api.v1.auth.delete_account`

**Purpose:** authenticated confirmation boundary for Accounts-owned 30-day recoverable deletion.  
**HTTP / authentication:** `POST`, authenticated.  
**Frontend usage:** destructive account deletion confirmation UI.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `confirmation` | string, max 16 | yes | — | must equal uppercase `DELETE` exactly |
| `reason` | string, max 300 | no | empty | trimmed; blank becomes absent |

Example: `{"confirmation":"DELETE","reason":"No longer using AOS"}`

Success: `200`, message `Account deleted successfully. You can restore it with email verification within the restore window.` Data includes `restore_window_days: 30`, lifecycle `status`, `idempotent`, `restore_deadline` when applicable, access-revocation summary and `cleanup`/feature summary from Accounts.

Stable errors: `AUTH_UNKNOWN_FIELD`, shared auth requirement, `VALIDATION_ERROR`, `RATE_LIMIT`, Accounts lifecycle stable errors, `DELETE_ACCOUNT_FAILED`.

Side effects: Auth locks the User and invokes `AccountLifecycleService.delete`; Accounts writes the profile tombstone/restore deadline, disables User and performs bounded feature access cleanup. Auth/session control revokes sessions and the endpoint clears current session cookies. Durable content is intentionally retained during the restore window.

Idempotency/retry: Accounts lifecycle delete is idempotent at the state transition level; repeated authorized deletion of an already-deleted profile returns deleted state while access revocation remains safe. After current session revocation a new request normally cannot authenticate as that deleted user.

Rate limiting: 3/hour per user + 10/hour per IP.

### `aos.api.v1.auth.request_restore_account`

**Purpose:** request account-restore OTP without revealing whether a restorable deleted account exists.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** deleted-account recovery entry screen.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |

Example: `{"email":"jane@example.com"}`

Success: `200`, `data: {}`, `If a restorable account exists for this email, a restore code has been sent.` Unknown, active/non-deleted, expired/non-restorable, cooldown-suppressed and eligible states share it.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `RATE_LIMIT`; downstream account/mail exceptions are logged while preserving the generic public acknowledgement.

Side effects: eligible restorable account outside cooldown gets account-restore OTP + Email Queue state. No-op states perform slow dummy OTP-hash work.

Idempotency/retry: safe within limits; 60-second resend cooldown suppresses duplicate mail.

Rate limiting: 10/hour per normalized email + 20/hour per IP.

### `aos.api.v1.auth.restore_account`

**Purpose:** consume restore OTP and invoke Accounts lifecycle restore.  
**HTTP / authentication:** `POST`, Guest.  
**Frontend usage:** deleted-account restore verification step.

| Parameter | Type | Required | Default | Validation |
|---|---|---:|---|---|
| `email` | string, max 140 | yes | — | trim/lowercase valid email |
| `otp` | string | yes | — | exactly six digits |

Example: `{"email":"jane@example.com","otp":"123456"}`

Success: `200`, `Account restored successfully. Please login.`, data includes `can_login: true`, Accounts lifecycle `status`, `idempotent`, and restore/feature summary.

Stable errors: `AUTH_UNKNOWN_FIELD`, `VALIDATION_ERROR`, `OTP_INVALID`, `ACCOUNT_NOT_DELETED`, `RESTORE_EXPIRED`, `RATE_LIMIT`, Accounts lifecycle errors, `RESTORE_ACCOUNT_FAILED`.

Side effects: User/challenge proof is locked and consumed, then Accounts restores profile state and re-enables User; durable state preserved during the grace window becomes visible according to owning feature rules. No session is created automatically.

Idempotency/retry: OTP proof is single-use. Once restored, login is a separate action; replayed proof is generic `OTP_INVALID`.

Rate limiting: 30/hour per normalized email + 60/hour per IP.

---

## E. Session contract

AOS uses Frappe database + Redis-backed sessions; Authentication does not implement a parallel token/session store.

### Web

- send `client_type: "web"`;
- login response intentionally omits `sid` from JSON;
- Frappe sets the `sid` cookie HttpOnly, `SameSite=Lax`, and Secure when the request is HTTPS;
- browser state-changing requests follow Frappe's CSRF/session rules;
- `me` never exposes the `sid` value.

### Mobile

- send `client_type: "mobile"`;
- login response intentionally includes `data.session.sid` so the native client can persist/use the Frappe session identifier through its secure session transport;
- the client must treat the value as a credential and never log/analytics-report it.

### Expiration

Session lifetime is framework/site configuration, not hard-coded in AOS. AOS does not emit a placeholder expiry timestamp; clients validate session state through `me` and treat `SESSION_INVALID` as authoritative.

### Logout and revocation

- logout is idempotent;
- password reset revokes every session;
- authenticated password change keeps the current `sid` and revokes the others;
- account deletion revokes account sessions through Accounts/Auth session control;
- revocation deletes DB session rows first and invalidates only their exact Redis session-hash fields after transaction commit.

No Authentication correctness rule requires sticky load-balancer sessions.

---

## F. Security model

### Passwords

- never stored/logged by AOS in plaintext;
- no custom password cryptography;
- Frappe's password hashing and verification are authoritative;
- AOS bounds password inputs to 128 characters and requires a baseline minimum of 8 before the Frappe User policy executes;
- signup/reset/change all pass through the same `validate_password_strength` entry plus Frappe User password validation.

### Enumeration prevention

- login unknown user and wrong password: identical `INVALID_CREDENTIALS` contract;
- unknown login performs slow password-hash work to reduce the obvious timing gap;
- duplicate registration performs dummy slow password-hash and OTP-hash work; generic recovery/resend/restore no-op branches perform dummy slow OTP-hash work, reducing simple account/cooldown timing oracles without persisting state;
- recovery request, verification resend and restore request use generic success messages independent of account/cooldown state;
- OTP verification collapses missing/wrong/expired/reused/exhausted state to `OTP_INVALID` at public verification boundaries;
- account state on password login is revealed only after password proof.

Registration is also enumeration-safe: new, existing and concurrent-duplicate addresses receive the same accepted acknowledgement after valid request-shape/rate-limit checks. Existing accounts are not mutated and no duplicate verification mail is sent.

### OTPs and reset tokens

- OTP generation: Python `secrets`, six decimal digits;
- OTP TTL: 10 minutes;
- maximum guesses per issued OTP: 5;
- OTP storage: Frappe slow password hash, never plaintext/fast bare SHA-256;
- resend internal cooldown: 60 seconds, deliberately hidden behind generic public responses;
- reset token: `secrets.token_urlsafe(32)`;
- reset-token TTL: 15 minutes;
- reset storage: SHA-256 digest of high-entropy token;
- reset comparison: `hmac.compare_digest`;
- successful OTP/reset credentials are single-use.

### Rate limiting

Security-sensitive Authentication endpoints use `aos.api.auth.rate_limits`, not process-local counters. The limiter runs an atomic Redis Lua `INCR` + first-window `EXPIRE` across all nodes and explicitly applies Frappe's site key prefix before executing Lua, so sites sharing Redis cannot consume one another's quotas. Redis failure returns `SERVICE_UNAVAILABLE` for the security-sensitive operation rather than silently disabling abuse protection.

IP limits are paired with identifier/subject/user limits where warranted. Identifier limits prevent a shared carrier/NAT IP from being the only abuse dimension; IP limits provide a broad spray bound. Non-IP dimension values are site-keyed HMAC-SHA-256 digested before they become Redis key material, so normalized emails, user IDs and OIDC subjects are not exposed by cache-key inspection. IPs remain readable for infrastructure troubleshooting.

Current fixed-window policy (all windows are one hour; the hidden resend cooldown is separate):

| Operation | Account/identity dimension | IP dimension |
|---|---:|---:|
| register | 5 / normalized email | 120 |
| verify signup email OTP | 30 / normalized email | 120 |
| resend signup email OTP | 10 / normalized email | 60 |
| password login | 20 / normalized email | 300 |
| 2FA continuation verify | 10 / challenge-token digest | 120 |
| forgot-password request | 10 / normalized email | 60 |
| forgot-password OTP verify | 30 / normalized email | 180 |
| forgot-password reset | 20 / normalized email | 120 |
| authenticated password change | 20 / user | 30 |
| Google login | 30 / provider subject after token proof | 300 |
| Apple login | 30 / provider subject after token proof | 300 |
| delete account | 3 / user | 10 |
| request account restore | 10 / normalized email | 20 |
| verify account restore | 30 / normalized email | 60 |

`me` and idempotent `logout` do not have an additional Authentication-specific Redis quota; they still depend on normal Frappe request/session controls. Rate limits are application defaults and should be tuned from production abuse/false-positive telemetry rather than increased merely to pass load tests.

Frappe's own login-attempt tracker remains in the enabled password-user authentication path.

### Logging

Never log plaintext or directly recoverable:

- passwords/current/new password;
- OTP values;
- reset tokens;
- Google/Apple ID tokens;
- session IDs/cookies;
- authorization credentials.

Auth security logs hash identifiers/users before logging. Password-policy/reuse failures log only exception classes, never tracebacks containing credential locals. Unexpected failures use safe Frappe diagnostics/redaction and return public codes/messages without secrets.

### OIDC

- RS256 only;
- provider HTTPS JWKS with bounded HTTP timeout and shared Redis cache;
- cache refresh once on unknown key ID for key rotation;
- PyJWT verifies signature, audience, expiration and required claims;
- issuer is explicitly allowlisted;
- Google requires verified email;
- immutable provider `sub` is persisted in `AOS Auth Identity` and is authoritative after binding;
- provider/JWKS outages are dependency failures, not invalid-token responses.

---

## G. Localization integration

Authentication reads/uses Localization in two places:

1. **New email/password registration** — optional `country`, `currency`, `language` hints are passed to the finalized Localization `resolve_guest_context` service. HTTP geo/language hints are consumed by that same Localization boundary when explicit values are absent.
2. **New social account creation** — the same optional hints and the same Localization resolution are used.

`login` and `me` read the existing `AOS User Preference` through the finalized preference/Localization serializer. They do not independently normalize codes, choose defaults, or create/repair preference rows.

A missing profile or preference on an established account is operational data corruption/configuration drift and returns `ACCOUNT_BOOTSTRAP_UNAVAILABLE`. This avoids surprising writes and default changes on every application bootstrap.

---

## H. Frontend contract

Frontend implementation must use only these canonical request keys:

- login: `identifier`, `password`, `client_type`;
- register: `email`, `password`, `full_name`, optional `country`, `currency`, `language`;
- social: `id_token`, `client_type`, optional first-account Localization hints;
- verification/recovery/restore: exact keys documented above;
- no compatibility aliases.

Frontend rules:

1. Treat `error` as the only machine-readable failure field.
2. For password login, place normalized email in `identifier`; do not send username/phone/email aliases.
3. Web must not persist/expose `sid` from JSON; it is omitted by contract and the Frappe HttpOnly cookie carries the session.
4. Mobile may use the explicit login `data.session.sid` as a credential; store it in secure storage and never log it.
5. Use `me` to validate/bootstrap an existing session. On `SESSION_INVALID`, clear local authenticated state.
6. Do not expect `me` to create preferences/profile state.
7. Treat registration, forgot-password request, verification resend and restore request success text as intentionally non-confirming; do not infer whether an account exists from those acknowledgements.
8. Do not retry successful login/social login blindly; a retry may create another Frappe session.
9. Safe retry targets include `me`, `logout`, and generic request/resend operations within rate limits.
10. After successful password reset, discard any local session and require login.
11. After password change, current session remains valid; other devices are signed out.
12. Display Localization from `data.preferences`; canonical keys are `country`, `currency`, `language`, `location`, `is_country_locked`. Do not separately recreate default selection rules inside Auth UI.
13. On `TWO_FACTOR_REQUIRED`, keep `data.challenge_token` only for the short login continuation, collect the six-digit OTP, and call `verify_two_factor` with the same intended `client_type`; do not log/persist the challenge longer than needed.
14. Social login sends provider `id_token`, not access-token aliases. Optional Localization hints are meaningful only if the provider identity creates a new AOS account.

---

## I. Removed legacy contracts

| Removed | Replacement | Frontend action required | Postman action required |
|---|---|---|---|
| password login by username/User.name | `identifier` containing email only | send email in `identifier` | update login examples/tests |
| login request aliases such as `email`, `username`, `usr` | exact `identifier` | remove aliases | remove aliases |
| arbitrary/ignored Auth kwargs | `AUTH_UNKNOWN_FIELD` | send exact keys only | remove old keys |
| duplicate registration `ALREADY_EXISTS` existence signal | same accepted registration acknowledgement for new/existing/racing normalized email | do not branch on account existence; direct existing users to login/recovery when needed | update duplicate-registration examples/tests |
| Localization hints on existing password login | existing authoritative User Preference | stop sending country/currency/language to login | remove from login request |
| login/`me` bootstrap repair | profile/preference invariant checks | ensure signup/social onboarding succeeds; handle invariant errors operationally | remove repair expectations |
| fast SHA-256 six-digit OTP storage | Frappe slow password hash in `otp_password_hash` | none | none |
| synchronous OTP provider send (`now=True`) | Frappe Email Queue in request transaction | UI can say queued/sent generically | update success text |
| public resend cooldown/account-active distinctions | generic resend success | do not branch on account state | update expected responses |
| password reset that left existing sessions alive | reset revokes all sessions | force re-login | assert re-login contract |
| wildcard Redis session deletion | exact `sid` hash invalidation after commit | none | none |
| social identity by mutable email alone | durable provider `sub` in AOS Auth Identity | no client change beyond valid ID token | no synthetic email identity assumptions |
| hand-written JWT/RSA parsing | PyJWT + JWKS | none | none |
| direct Frappe Website User `/api/method/login` | `aos.api.v1.auth.login` | never call generic Frappe login | remove generic Frappe login requests |
| Frappe public `sign_up` / reset-key recovery / Website User password methods | versioned AOS registration/recovery/change-password endpoints; generic paths guarded | never call those Frappe Website auth methods | remove them from collections |
| historical `add_auth_indexes` migration/dedupe patch | final DocType schema/invariants | none | none |
| historical `finalize_auth_identity_privacy` upgrade transformer | final privacy-safe `AOS Auth Identity` / `AOS Auth Challenge` DocTypes directly on clean install | none | remove any assumption that old identity/email-verification data is migrated |
| password-change `logout_all` option | fixed policy: keep current session, revoke all other sessions | remove toggle/field | remove `logout_all` |
| OTP aliases such as `code` / `verification_code` | exact `otp` key | send only `otp` | remove aliases |
| social-login `access_token` alias | provider `id_token` only | send ID token only | remove access-token field |
| 2FA `remember_me` or other session-option aliases | exact `challenge_token`, `otp`, `client_type` | remove unsupported options | remove unsupported fields |

---

## J. Patches / installation

AOS is targeting a **new-site installation**, so Authentication final state comes from authoritative DocType/controller/hook/install definitions rather than historical upgrade transformations.

### Removed Authentication patches

#### `aos.patches.v1_0.add_auth_indexes`

Action: **REMOVE** (already absent from the repository and patch list).

Reason: it belonged to an older Authentication schema, deduplicated historical challenge rows and installed indexes/uniqueness after data already existed. A fresh site cannot contain that historical state.

Permanent replacement:

- `AOS Auth Challenge` uses deterministic `authc-<sha256(user + NUL + purpose)>` primary naming with renaming disabled, so one row per user/purpose is intrinsic to the final model;
- `AOS Auth Challenge.user` and `continuation_token_hash` carry permanent `search_index` metadata;
- final OTP/token fields live directly in the DocType.

#### `aos.patches.v1_0.finalize_auth_identity_privacy`

Action: **REMOVE**.

Reason: this patch existed only to transform previously persisted social identity rows from the historical raw-provider-subject/email schema, rename old identity documents, and clear retired `AOS Email Verification` records. None of those historical records/columns can exist on a clean AOS installation using the current DocType source. Keeping the patch would make the new-site path carry upgrade baggage with no final-state responsibility.

Permanent replacement:

- `AOS Auth Identity` directly stores only `provider`, `user`, and the opaque unique `user_provider_key`;
- its primary name is generated from a site-keyed HMAC of provider + OIDC subject;
- raw provider subject/email are never part of the final schema;
- `AOS Auth Challenge` is the only Authentication temporary challenge model.

### Remaining Authentication-specific patches

**None.** Authentication no longer requires a historical data-transformation patch to reach its final schema on a fresh site.

### Cross-feature patch consumed by Authentication

`aos.patches.v1_0.install_localization_schema` is **KEEP**, but it is Localization-owned rather than Authentication-owned. It installs/defensively verifies final database structures that are not fully expressible as DocType field metadata: the composite AOS Location uniqueness/read index and the one-user preference uniqueness contract. Authentication consumes `AOS User Preference` through the finalized Localization service, so this patch remains genuinely required for the clean-site Localization data model; it is not retained for old Authentication compatibility.

`aos.patches.v1_0.harden_accounts_subsystem` remains in the repository because it belongs to the separately owned Accounts feature. Authentication does not rely on that patch as its installation mechanism, and this Authentication review does not broaden scope by deleting Accounts migration history.

### Permanent fresh-site definitions

- Auth DocType schema/field indexes/permissions: `aos/aos/doctype/aos_auth_challenge/*` and `aos/aos/doctype/aos_auth_identity/*`;
- User/Profile/Preference uniqueness: authoritative owning DocTypes plus the Localization final-state uniqueness verification above;
- generic Frappe signup disabled: `aos.install.after_install` sets `Website Settings.disable_signup = 1`;
- generic Website User auth/recovery bypasses guarded: `hooks.py` `on_login`, `extend_doctype_class`, and `override_whitelisted_methods`;
- no ad-hoc Authentication install SQL is used.

Fresh-install expectation:

```text
bench new-site ...
bench --site <site> install-app aos
bench --site <site> migrate
```

must derive Authentication DocTypes, permissions, hooks and final schema from the app definitions above. Authentication does not depend on an upgrade-only data transformation.

---

## K. Scalability / high availability

### Application-level scalability

- no process-local mutable Authentication state is required for correctness;
- sessions are Frappe DB/Redis state shared across workers/nodes;
- abuse counters are atomic shared Redis state;
- canonical AOS Website User creation is not capped by Frappe's coarse site-wide User-creation counter; a narrow `User` mixin bypasses only that upstream guard after AOS Redis rate limits have passed, while generic Frappe/Desk User creation remains throttled;
- password/reset/account mutation ordering uses a per-user database row lock, not a global application lock;
- OTP rows are bounded per user/purpose instead of append-only;
- `me` is read-oriented and never creates/repairs data; login and `me` load the required Localization preference once and reuse it during serialization instead of performing an existence check followed by an immediate duplicate read;
- password/session cache invalidation targets exact session IDs;
- email is queued rather than synchronously delivered from the Auth web worker; repeated 2FA first-factor success inside the resend cooldown reuses a still-valid OTP while rotating the continuation token, preventing avoidable mail floods;
- OIDC JWKS is shared-cached in Redis; unknown-key refresh is globally throttled per site/provider with an atomic Redis NX guard to prevent refresh storms;
- credential verification is deliberately not cached;
- recoverable account deletion never synchronously deletes follower/content graphs; visibility is controlled by the single account tombstone;
- permanent cleanup starts only after the 30-day deadline and is resumable through `purge_status`; follow/block cleanup is capped at 10,000 rows per account/run;
- seller, Ads and Shorts public reads independently require an active/non-deleted owner, so preserved content cannot leak while the owner is tombstoned.

### Horizontal-scaling verdict

Authentication correctness does not rely on sticky sessions, Python globals, a particular web worker, or local-only locks. Requests can move between application nodes so long as they share the configured Frappe database and Redis infrastructure.

### Concurrency safeguards

- User row is the first account-level lock for session/password/account mutations;
- verification row locks serialize OTP attempts/consumption and resend state;
- deterministic verification names + DB primary key prevent duplicate verification rows;
- User/Profile/User Preference schema uniqueness prevents duplicate account/bootstrap records;
- AOS Auth Identity deterministic identity + unique per-user/provider key protects social-login races/rebinding;
- duplicate signup retries/races map to the same enumeration-safe accepted acknowledgement while DB uniqueness prevents duplicate accounts;
- logout is idempotent and repeated token consumption is stable failure.

### High-frequency endpoint assessment

| Area | login | `me` | signup |
|---|---|---|---|
| DB queries/request | bounded exact User/account/bootstrap reads + Frappe auth/session work | bounded User/Profile/Preference/role/seller bootstrap reads + Frappe session bookkeeping | User existence + User/Profile/Preference/verification inserts and Localization resolution |
| Writes/request | Frappe session + normal login metadata | no AOS repair writes; Frappe may refresh session metadata | User/Profile/Preference/verification + Email Queue |
| Cache usage | Redis rate limit, Frappe auth/session caches | Frappe session + preference/role caches | Redis rate limit + Localization caches |
| Index coverage | exact email/User primary lookup; Profile/User Preference deterministic identity | User/Profile/User Preference identities; seller-owned indexes | User uniqueness; Profile/Preference uniqueness; deterministic verification primary key |
| Lock contention | one User row per account during credential/session transition | none from AOS Auth | uniqueness/insert-level DB contention only; no Frappe site-wide signup throttle serialization/cap on managed AOS Website User creation |
| Payload size | small bootstrap allowlist | small bootstrap allowlist | small acknowledgement |
| Horizontal scaling | shared DB/Redis | shared DB/Redis | shared DB/Redis + Email Queue |
| Abuse risk | very high; identifier + NAT-tolerant IP limiting + Frappe tracker | lower; authenticated | high; email + NAT-tolerant IP limiting + DB uniqueness |
| Main bottleneck | password hashing + DB/session capacity | session/bootstrap DB/cache reads | password hashing, DB writes, queue throughput |

The serializer still calls Accounts/Seller/Localization-owned bootstrap helpers. Those owning services determine part of `me`'s final query count. Production observability/load tests should measure end-to-end query counts on the actual Frappe/MariaDB/Redis deployment rather than treating source inspection as a throughput benchmark.

### Infrastructure-level capacity

The application changes do **not** prove capacity for one million users or one million simultaneous sessions. Production-like tests/sizing remain required for:

- Frappe web workers and background email workers;
- reverse proxy/load balancer capacity and timeouts;
- Redis HA, memory, latency, persistence/failover choices and connection limits;
- MariaDB capacity, indexes, connections, replicas/HA/failover and transaction latency;
- Frappe session configuration and simultaneous-session policy;
- outbound email provider/relay capacity;
- OIDC egress and DNS/network resilience;
- regional latency and CDN/edge architecture where relevant;
- metrics, traces, security alerting and rate-limit dashboards;
- release/login burst scenarios, reconnect storms and retry behavior.

### Dependency failure classification

| Dependency | Required? | Failure behavior |
|---|---:|---|
| database | required | fail; never authenticate from stale state |
| Redis Auth rate limiter | required for protected Auth request | `SERVICE_UNAVAILABLE`; fail closed |
| Frappe session Redis/cache | required according to framework session config | framework/session failure; no invented local fallback |
| Localization during new account creation | required | account creation rolls back/fails |
| Localization read for established bootstrap | required invariant | safe preference/invariant failure |
| profile avatar/media URL | optional presentation data | degrades to `avatar: null` and logs only secret-safe failure classification |
| Email provider delivery | asynchronous/degradable after queueing | request depends on durable queue creation, not provider round trip |
| Google/Apple JWKS/provider network on cache miss | required for social proof | `SERVICE_UNAVAILABLE`; no unverified login |

---

## L. Testing

Authentication-focused test modules:

- `aos/api/accounts/tests/test_recoverable_deletion.py` — reversible social/verification preservation plus bounded permanent purge;
- `aos/api/auth/tests/test_session_api.py` — exact login contract, enumeration safety, disabled/deleted behavior, mobile/web session shape, read-only `me`, logout idempotency;
- `test_register_api.py` — validation, schema creation, Localization bootstrap, enumeration-safe duplicate/race retry handling, rollback on queued-mail failure;
- `test_otp_api.py` — generic failures, activation, replay, resend enumeration resistance;
- `test_password_reset_api.py` — recovery enumeration safety, OTP exchange, expiry, token replay and reset behavior;
- `test_password_change_api.py` — current-password proof, configured policy/reuse behavior and session revocation policy;
- `test_social_login_api.py` — provider subject binding, Apple later-login behavior, invariant checks, anti-rebinding, canonical fields;
- `test_two_factor_api.py` — continuation-token/OTP consumption, replay safety, session creation and resend-cooldown mail suppression;
- `test_delete_restore_api.py` — delete/restore validation and generic restore proof errors;
- `test_auth_security_contracts.py` — site-namespaced atomic Redis limiter/fail-closed contract, generic Frappe login + parallel auth-path guards, install default, source safety invariants;
- `aos/tests/test_auth_database_contracts.py` — final fresh-site DocType/security/locking/patch contracts.

Recommended focused command from `frappe-bench`:

```bash
bench run-tests --app aos --module aos.api.auth.tests.test_session_api
bench run-tests --app aos --module aos.api.auth.tests.test_register_api
bench run-tests --app aos --module aos.api.auth.tests.test_otp_api
bench run-tests --app aos --module aos.api.auth.tests.test_password_reset_api
bench run-tests --app aos --module aos.api.auth.tests.test_password_change_api
bench run-tests --app aos --module aos.api.auth.tests.test_social_login_api
bench run-tests --app aos --module aos.api.auth.tests.test_two_factor_api
bench run-tests --app aos --module aos.api.auth.tests.test_delete_restore_api
bench run-tests --app aos --module aos.api.auth.tests.test_auth_security_contracts
bench run-tests --app aos --module aos.tests.test_auth_database_contracts
```

Full backend command:

```bash
bench run-tests --app aos
```

Fresh-site verification should run install/migrate before the focused/full suite. Source-only checks such as Python compilation, JSON parsing, API-documentation validation, and forbidden-pattern scans are useful but are not substitutes for the Frappe/MariaDB/Redis tests above.
