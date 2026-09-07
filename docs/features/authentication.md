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
    |       +--> AOS Email Verification
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
| `account_helpers.py` | new-user AOS Profile + Localization bootstrap and invariant checks |
| `session_control.py` | exact server-session/token revocation without wildcard cache deletion |
| `session_hooks.py` | prevents generic Frappe Website User login bypass |
| `framework_guards.py` | closes parallel Frappe Website signup/recovery/password endpoints while preserving System User Desk/admin recovery and password administration |
| `rate_limits.py` | atomic Redis INCR+EXPIRE via Lua; fail-closed dependency behavior |
| `locking.py` | account-scoped `User` row lock used by security mutations/session creation |
| `serializers.py` | allowlisted user/session/preferences/roles/seller response shape |
| `contracts.py` | rejects deprecated aliases and unknown request keys |
| `validators.py` | strict strings, email normalization, client type and password input bounds plus Frappe configured strength-policy delegation |
| `aos/install.py` | fresh-site default disabling Frappe public signup |

---

## C. Data model

```text
Frappe User
 |-- 1:1 AOS Profile
 |-- 1:1 AOS User Preference  -> Localization masters/rules
 |-- 0..3 AOS Email Verification (one deterministic row per purpose)
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

Lifecycle: created disabled for password signup, enabled after email verification; social-created users are enabled after verified provider identity. Account deletion/restoration is owned by Accounts lifecycle and Auth consumes the resulting state.

### `AOS Profile`

Purpose: AOS account/profile state required by all marketplace identity surfaces.

Authentication relies on the existing profile contract. The `user` identity is unique through the DocType naming/field definition. `account_status`, `is_deleted`, and `restore_deadline` are permanently search-indexed in the final DocType for account-state/recovery lookups.

Authentication does not repair a missing profile during login or `me`; a missing row is an internal account-bootstrap invariant failure (`ACCOUNT_BOOTSTRAP_UNAVAILABLE`).

### `AOS User Preference`

Purpose: user-owned Localization selection.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `user` | Link User | yes | unique / deterministic name | one preference per AOS user |
| `country` | Link/master value | yes | schema-defined | Localization country |
| `language` | Link/master value | yes | schema-defined | Localization language |
| `currency` | Link/master value | yes | schema-defined | Localization currency |

Creation/default resolution belongs to Localization. Authentication initializes this row only while creating a new user. Missing preference on an existing account returns the single public invariant error `ACCOUNT_BOOTSTRAP_UNAVAILABLE` and is not silently recreated on a high-frequency request.

### `AOS Email Verification`

Purpose: bounded temporary state for email verification, password recovery and account restoration. There is one deterministic document name per `User + purpose`; repeated sends overwrite the same row rather than creating unbounded OTP history.

Valid purposes: `email_verification`, `password_reset`, `account_restore`.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `user` | Link User | yes | search index + input to deterministic primary-name hash | owner of security state and efficient account-wide revocation |
| `email` | Data / Email | yes | no | delivery address snapshot; not queried as an Authentication hot-path key |
| `purpose` | Select | yes | input to deterministic primary-name hash | isolates verification flows |
| `otp_password_hash` | Data, hidden/read-only | no | no | slow Frappe password hash of current OTP; never stores OTP plaintext |
| `expires_at` | Datetime | no | no | OTP expiry |
| `last_sent_at` | Datetime | no | no | internal resend cooldown |
| `attempts` | Int | no | no | bounded OTP guesses for current code |
| `reset_token_hash` | Data, hidden/read-only | no | no | SHA-256 digest of high-entropy reset token |
| `reset_token_expires_at` | Datetime | no | no | reset-token expiry |
| `is_used` | Check | no | no | one-time OTP consumption marker |

Invariants:

- document name is `authv-<sha256(user + NUL + purpose)>` and renaming is disabled; the fixed-length key avoids Frappe name-length issues for long email addresses;
- OTP code is generated with `secrets` and slow-hashed with Frappe's password hashing context;
- reset token is high entropy, stored only as a digest and compared in constant time;
- OTP attempts are serialized by row lock;
- successful consumption clears the OTP verifier;
- password reset clears reset-token state and revokes sessions;
- System Manager has read/report-only Desk access; Authentication service code owns mutation via controlled server-side paths.

### `AOS Auth Identity`

Purpose: durable social identity mapping using the immutable OIDC provider subject instead of mutable/reassignable email.

| Field | Type | Required | Unique/indexed | Purpose |
|---|---|---:|---:|---|
| `provider` | Select (`google`, `apple`) | yes | standard filter | identity provider |
| `subject` | Data(255), hidden/read-only | yes | deterministic primary name | provider's immutable `sub` claim |
| `user` | Link User | yes | search index | bound AOS user |
| `email_at_link` | Email/Data(254), read-only | no | no | audit/debug snapshot; never used as durable identity |
| `user_provider_key` | Data, hidden/read-only | yes | unique | prevents one AOS user from silently binding multiple subjects for the same provider |

The primary document name is a SHA-256-derived identifier from provider + subject. `user_provider_key` is also derived and unique. A conflicting identity is never silently rebound during login. System Manager has read/report-only Desk access; Authentication service code owns creation/mutation.

### Frappe `Sessions`

Framework-owned session persistence. AOS queries `sid` values only for exact user-scoped revocation. Database deletion is done inside the surrounding transaction; exact Redis `session` hash entries are invalidated after commit. Wildcard Redis key deletion is prohibited.

---

## D. Endpoint reference

All public endpoints use `/api/method/aos.api.v1.auth.<method>` and the standard AOS envelope:

Success:

```json
{"ok": true, "message": "...", "data": {}}
```

Failure:

```json
{"ok": false, "message": "...", "error": "STABLE_CODE", "data": {}}
```

`error` is the only machine-readable error key. Raw Frappe/provider/SQL/Redis exception text is never a public contract. Frappe's RPC dispatcher injects its internal `cmd` selector into `form_dict`; the v1 Authentication boundary strips that transport-only key before request-contract validation. All other unknown client fields remain rejected with `AUTH_UNKNOWN_FIELD`.

### `aos.api.v1.auth.register`

Purpose: create a password account and queue email verification.

Authentication: Guest. Frontend: registration screen.

Inputs:

| Parameter | Type | Required | Default | Description |
|---|---|---:|---|---|
| `email` | string <=140 | yes | — | trimmed/lowercased valid email |
| `password` | string 8..128 | yes | — | passed to Frappe password policy/hashing |
| `full_name` | string <=140 | yes | — | whitespace-normalized display seed |
| `country` | string <=140 | no | Localization resolution | optional new-user hint |
| `currency` | string <=32 | no | Localization resolution | optional new-user hint |
| `language` | string <=140 | no | Localization resolution | optional new-user hint |

Example:

```json
{"email":"jane@example.com","password":"StrongPass123!","full_name":"Jane Doe","country":"Kenya","currency":"KES","language":"en"}
```

Success: `200`, `If this email can be registered, a verification code has been queued.` The same acknowledgement is returned for an already-existing email or a concurrent duplicate insert; no second account or email is created/sent.

Important errors: `VALIDATION_ERROR`, `AUTH_UNKNOWN_FIELD`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`, `REGISTER_FAILED`. Account existence/lifecycle is not disclosed by registration.

Side effects: creates disabled User + AOS Profile + AOS User Preference + verification state + Frappe Email Queue row in one request transaction. A queue failure rolls the account creation back. No provider network call is made synchronously.

Idempotency: duplicate/retried requests for an already-existing normalized email are safely acknowledged without creating, mutating or re-sending. Database uniqueness is the final concurrent-race guard.

Rate limit: shared Redis: 5/hour per normalized email and 120/hour per IP. The email dimension protects one account while the substantially higher IP dimension reduces shared-NAT/mobile-carrier collateral blocking.

### `aos.api.v1.auth.verify_email_otp`

Purpose: consume email verification and enable account.

Authentication: Guest. Frontend: signup verification screen.

Inputs: `email` (required normalized email), `otp` (required string <=12; issued value is six digits).

Example: `{"email":"jane@example.com","otp":"123456"}`

Success: `Email verified. Account activated.`

Public verification failures collapse to `OTP_INVALID` / `Invalid or expired OTP.` so missing, wrong, expired, already-used and exhausted records do not reveal state. Shared Redis limits apply by identifier and IP. OTP state and User activation are ordered by account + verification row locks.

Idempotency: successful token is single-use; a replay returns the stable generic OTP failure.

### `aos.api.v1.auth.resend_email_otp`

Purpose: queue a replacement signup verification code when appropriate.

Authentication: Guest. Input: `email` only.

Success for unknown, already-active, cooldown-suppressed, and eligible account uses the same public message: `If verification is required for this email, a code has been queued.`

This intentionally prevents account/cooldown enumeration. Shared Redis abuse limits still return `RATE_LIMIT` independent of account existence.

### `aos.api.v1.auth.login`

Purpose: canonical password login.

Authentication: Guest. Frontend: login screen.

Inputs:

| Parameter | Type | Required | Accepted |
|---|---|---:|---|
| `identifier` | email string <=140 | yes | email only; normalized lowercase |
| `password` | string <=128 | yes | Frappe password verifier |
| `client_type` | string | yes | `web` or `mobile` |

No `email`, `username`, `usr`, `remember_me`, localization hints or other aliases are accepted.

Example:

```json
{"identifier":"jane@example.com","password":"StrongPass123!","client_type":"mobile"}
```

Mobile success contains `data.session.sid`; web success does not. Both contain `session`, `user`, `preferences`, `roles`, and `seller` bootstrap sections.

Unknown user and wrong password both return exactly `INVALID_CREDENTIALS` / `Invalid credentials.`. Account-deleted/suspended/disabled/pending-verification state is disclosed only after credential proof.

Side effects: Frappe session creation and normal Frappe login metadata updates. Existing AOS profile/preferences are never repaired here.

Idempotency: non-idempotent session creation; safe client retry can create another valid session depending on Frappe simultaneous-session settings. Do not retry blindly after a response is received.

Rate limit: shared Redis: 20/hour per normalized identifier and 300/hour per IP; Frappe's own login-attempt tracker also remains in the credential path for enabled users. The higher IP ceiling intentionally avoids making carrier/shared NAT the primary lockout boundary.

### `aos.api.v1.auth.google_login`

Purpose: login/register with verified Google OIDC identity.

Authentication: Guest. Inputs: `id_token`, `client_type`; `country`, `currency`, `language` are optional only for first account creation.

The verifier requires RS256 signature, current Google JWKS, allowed configured audience, valid issuer/expiry/subject, email, and verified email. Provider `sub` is the durable identity.

Success/session shape matches password login. Invalid tokens return `TOKEN_INVALID`/`TOKEN_EXPIRED`; missing configuration or JWKS/provider dependency failures are `CONFIG_ERROR`/`SERVICE_UNAVAILABLE` and are not misreported as bad credentials.

### `aos.api.v1.auth.apple_login`

Purpose: login/register with Apple OIDC identity.

Authentication and request shape match Google. First sign-in must provide an email if there is no existing subject binding, and any Apple email present must carry a provider-verified `email_verified` claim. Later Apple tokens may omit email because `AOS Auth Identity` resolves the already-bound immutable `sub`.

### `aos.api.v1.auth.me`

Purpose: high-frequency current-user/session bootstrap at application startup/resume.

Authentication: a valid session is required logically; decorator allows Guest only so the endpoint can return stable `SESSION_INVALID` JSON.

Inputs: none. Unknown query/body fields are rejected.

Success:

```json
{
  "ok": true,
  "message": "Session fetched.",
  "data": {
    "session": {"authenticated": true, "expires_at": null},
    "user": {
      "id": "<public-account-id>",
      "email": "jane@example.com",
      "full_name": "Jane Doe",
      "first_name": "Jane",
      "last_name": "Doe",
      "user_image": null,
      "enabled": true,
      "account_status": "Active"
    },
    "preferences": {"country":"Kenya","language":"en","currency":"KES"},
    "roles": ["Website User"],
    "seller": {}
  }
}
```

`sid` is never returned by `me`. Missing AOS Profile/User Preference is an invariant failure and is not repaired by this read endpoint.

Idempotency: yes; it is read-oriented apart from normal framework session activity/expiry bookkeeping.

### `aos.api.v1.auth.logout`

Purpose: clear current Frappe session.

Authentication: Guest allowed for an idempotent result. Inputs: none.

Success when authenticated: `Logged out successfully.` Success when already Guest/expired: `Already logged out.`

Idempotency: yes.

### `aos.api.v1.auth.forgot_password_request`

Purpose: request recovery OTP without revealing account existence.

Authentication: Guest. Input: `email` only.

All unknown/deleted/eligible/cooldown-suppressed account states return the same success message: `If an account exists for this email, a recovery code has been queued.` Eligible sends write OTP state + Email Queue state transactionally.

Rate limit: shared Redis per identifier and generous IP dimension.

Idempotency: safe to retry within limits; internal cooldown may suppress duplicate delivery without changing the public success response.

### `aos.api.v1.auth.forgot_password_verify_otp`

Purpose: exchange a valid recovery OTP for a high-entropy reset token.

Authentication: Guest. Inputs: `email`, `otp`.

Success: `{"reset_token":"<one-time-token>"}`. The plaintext token is returned once to the requesting client; only a SHA-256 digest is stored server-side. Generic OTP failure is used for unknown/wrong/expired/reused state.

### `aos.api.v1.auth.forgot_password_reset`

Purpose: consume reset token and replace password.

Authentication: Guest.

Inputs: `email`, `reset_token`, `new_password`, `confirm_password`.

Success: `Password updated successfully. Please login again.`

Important errors: `TOKEN_INVALID`, `TOKEN_EXPIRED`, `PASSWORD_MISMATCH`, `PASSWORD_REUSED`, `VALIDATION_ERROR`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`. Password-policy/reuse details are returned only after a valid reset token has been proven, so they cannot be used to enumerate accounts.

Side effects: password hash update, reset-token consumption and deletion of all Frappe sessions in the same DB transaction. Exact Redis session entries are invalidated after commit.

Idempotency: token is single-use; retry after success returns `TOKEN_INVALID`.

### `aos.api.v1.auth.change_password`

Purpose: authenticated password replacement.

Authentication: Authenticated session.

Inputs: `current_password`, `new_password`, `confirm_password`.

Success: `Password changed successfully.`

The current password is verified through Frappe. The current session is retained while other server sessions are revoked. Password validation uses the same AOS baseline + configured Frappe strength policy as signup/reset, and the current password cannot be reused (`PASSWORD_REUSED`).

### `aos.api.v1.auth.delete_account`

Purpose: authenticated confirmation boundary for Accounts-owned recoverable deletion.

Authentication: Authenticated. Inputs: `confirmation` exactly `DELETE`; optional `reason` <=300.

Account lifecycle/cleanup semantics are documented by the Accounts feature. Auth adds strict confirmation, abuse limits, user-row ordering, safe error shaping, and clears the current session cookie after lifecycle access revocation.

### `aos.api.v1.auth.request_restore_account`

Purpose: request a restore OTP without revealing whether a restorable deleted account exists.

Authentication: Guest. Input: `email`. Generic success is returned for unknown/non-deleted/expired/cooldown-suppressed/eligible states. Abuse limiting is shared Redis by email and IP.

### `aos.api.v1.auth.restore_account`

Purpose: verify restore OTP and invoke Accounts lifecycle restore.

Authentication: Guest. Inputs: `email`, `otp`.

Unknown/missing/wrong/expired/reused verification state returns generic `OTP_INVALID`. Once valid proof exists, lifecycle-specific state such as `RESTORE_EXPIRED` may be returned.

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

Session lifetime is framework/site configuration, not hard-coded in AOS. `data.session.expires_at` is currently `null`; clients must rely on session validity rather than calculating an independent expiry.

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

IP limits are paired with identifier/subject/user limits where warranted. Identifier limits prevent a shared carrier/NAT IP from being the only abuse dimension; IP limits provide a broad spray bound.

Current fixed-window policy (all windows are one hour; the hidden resend cooldown is separate):

| Operation | Account/identity dimension | IP dimension |
|---|---:|---:|
| register | 5 / normalized email | 120 |
| verify signup email OTP | 30 / normalized email | 120 |
| resend signup email OTP | 10 / normalized email | 60 |
| password login | 20 / normalized email | 300 |
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
12. Display Localization from `data.preferences`; do not separately recreate default selection rules inside Auth UI.

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

---

## J. Patches / installation

Deployment target is a new Frappe site. Authentication therefore optimizes the final schema rather than preserving migrations for AOS installations that will never exist.

### Removed

`aos.patches.v1_0.add_auth_indexes`

Reason: it deduplicated historical `AOS Email Verification` data and created post-install indexes/uniqueness needed by an earlier schema. A fresh site cannot contain those historical duplicates. The file and `patches.txt` entry are removed.

Permanent state moved into final definitions:

- `AOS Email Verification` uses deterministic hashed `user + purpose` primary naming and renaming is disabled, so one row per purpose is intrinsic to a fresh schema;
- `otp_password_hash` replaces the historical `otp_hash` field directly in the DocType;
- `AOS Auth Identity` carries permanent unique social identity constraints in its DocType;
- `AOS Email Verification.user` is permanently indexed for account-wide token revocation;
- `after_install` sets `Website Settings.disable_signup = 1`; hook overrides also guard the generic Frappe signup/recovery/password methods, so the versioned AOS contract is the only public Website User path.

### Remaining Authentication-specific patches

None.

Other patches in `patches.txt` belong to other finalized features (Accounts, Localization, etc.) and were not removed merely because Authentication consumes those features. Their necessity must be evaluated by their owning feature's fresh-site review.

Fresh-install expectation:

```text
bench new-site ...
bench --site <site> install-app aos
bench --site <site> migrate
```

must derive Authentication DocTypes, permissions, hooks and final schema directly from the app definitions; Authentication does not depend on an upgrade-only data transformation.

---

## K. Scalability / high availability

### Application-level scalability

- no process-local mutable Authentication state is required for correctness;
- sessions are Frappe DB/Redis state shared across workers/nodes;
- abuse counters are atomic shared Redis state;
- password/reset/account mutation ordering uses a per-user database row lock, not a global application lock;
- OTP rows are bounded per user/purpose instead of append-only;
- `me` is read-oriented and no longer creates/repairs data;
- password/session cache invalidation targets exact session IDs;
- email is queued rather than synchronously delivered from the Auth web worker;
- OIDC JWKS is shared-cached in Redis; unknown-key refresh is globally throttled per site/provider with an atomic Redis NX guard to prevent refresh storms;
- credential verification is deliberately not cached.

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
| Lock contention | one User row per account during credential/session transition | none from AOS Auth | uniqueness/insert-level DB contention only |
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
| Email provider delivery | asynchronous/degradable after queueing | request depends on durable queue creation, not provider round trip |
| Google/Apple JWKS/provider network on cache miss | required for social proof | `SERVICE_UNAVAILABLE`; no unverified login |

---

## L. Testing

Authentication-focused test modules:

- `aos/api/auth/tests/test_session_api.py` — exact login contract, enumeration safety, disabled/deleted behavior, mobile/web session shape, read-only `me`, logout idempotency;
- `test_register_api.py` — validation, schema creation, Localization bootstrap, enumeration-safe duplicate/race retry handling, rollback on queued-mail failure;
- `test_otp_api.py` — generic failures, activation, replay, resend enumeration resistance;
- `test_password_reset_api.py` — recovery enumeration safety, OTP exchange, expiry, token replay and reset behavior;
- `test_password_change_api.py` — current-password proof, configured policy/reuse behavior and session revocation policy;
- `test_social_login_api.py` — provider subject binding, Apple later-login behavior, invariant checks, anti-rebinding, canonical fields;
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
bench run-tests --app aos --module aos.api.auth.tests.test_delete_restore_api
bench run-tests --app aos --module aos.api.auth.tests.test_auth_security_contracts
bench run-tests --app aos --module aos.tests.test_auth_database_contracts
```

Full backend command:

```bash
bench run-tests --app aos
```

Fresh-site verification should run install/migrate before the focused/full suite. Source-only checks such as Python compilation, JSON parsing, API-documentation validation, and forbidden-pattern scans are useful but are not substitutes for the Frappe/MariaDB/Redis tests above.
