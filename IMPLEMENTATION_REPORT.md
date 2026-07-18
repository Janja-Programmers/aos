# Shared Production Foundation — Checkpoint 1 correction report

Date: 2026-07-18  
Correction artifact: `aos-foundation-01-ci-v2.zip`  
Readiness: **NOT READY** — mandatory package-dependent and Frappe runtime gates
did not execute in this environment. See `TEST_RESULTS.md`.

## Scope completed

This correction keeps Checkpoint 1 limited to CI and automated testing. It
contains the complete backend source, eight FastAPI services, exact/hash-locked
dependency definitions, a consolidated non-deploying workflow, local
equivalents, tests, Compose/infrastructure policy, Dependabot, and evidence
documents. It does not implement deployment, secrets/configuration hardening,
observability, migrations, backups, rate limiting, error-handling redesign, or
an outbox.

The configured foundation is fail-closed. Its presence is not presented as a
successful release gate: Semgrep, pip-audit, the eight pytest suites, the eight
clean production installations, and the disposable-site Frappe run remain
`NOT RUN` after Python package downloads repeatedly timed out.

## Exact correction file inventory

`ci/checkpoint1-v2-changed-files.txt` is the machine-readable, exact
old-v1-to-v2 path inventory captured immediately before packaging. The
functional groups are:

- Workflow/policy: `.github/workflows/ci.yml`, `.pre-commit-config.yaml`,
  `ci/validate_actions.py`, `ci/validate_foundation.py`,
  `ci/validate_repository.py`, `ci/repository-quality.sh`,
  `ci/repository-hygiene-and-secrets.sh`, `ci/validate-compose.sh`,
  `ci/validate-infrastructure.sh`, `ci/compose_policy.py`, and
  `ci/versions.env`.
- New correction controls/evidence: `.secrets.baseline`,
  `ARTIFACTS_MANIFEST.md`, `ci/compare_secret_baseline.py`,
  `ci/coverage-floors.env`, `ci/image-manifest-evidence.txt`,
  `ci/install-compose-tools.sh`, `ci/install-infra-tools.sh`,
  `ci/validate_lock_credentials.py`, `ci/verify-image-manifests.sh`, and
  `ci/verify-upstream-pins.sh`.
- Maintained configuration/docs: `docker-compose.yml`, `ci/compose.env`,
  `README.md`, `docs/development/testing.md`, `docs/production/ci-cd.md`,
  `docs/production/release-checklist.md`, `docs/production/maps.md`,
  `infra/maps/manifest.env.example`, `infra/maps/photon/Dockerfile`,
  `infra/maps/scripts/build-map-fonts.sh`, and
  `infra/maps/scripts/build-photon-image.sh`.
- Service tests/locks: the five new `tests/test_worker.py` files for analytics,
  moderation, notification delivery, search ranking, and video processing;
  boundary-test additions for background removal, image search, and
  translation; and the corresponding exact test requirement sources/locks.
- Existing AOS source: every v1 formatting/modernization edit under `aos/` was
  restored from the complete authoritative source tree. The result is
  byte-identical to that source except seven newline-only differences listed
  below; there are no retained AOS application behavior changes.

### Files added

All paths marked `A` in `ci/checkpoint1-v2-changed-files.txt`; these are the
reviewed secret baseline, artifact manifest, correction validators/installers,
coverage/image evidence, five worker test modules, and this exact inventory.

### Files modified

All paths marked `M` in that inventory. This includes the CI/config/docs/test
corrections and the v1-to-authoritative-source restoration under `aos/` and the
service app directories.

### Files deleted

No additional path was deleted by v2. The previously obsolete
`.github/workflows/linter.yml` deletion is intentionally preserved from the v1
workflow consolidation and is documented in the migration below.

## Existing GitHub Actions migration

The repository already contained `.github/workflows/ci.yml` and
`.github/workflows/linter.yml`. The original CI used mutable Actions, floating
services and Bench/Frappe installation, while the original linter had a manual
trigger that skipped its primary job and floating Semgrep/audit inputs.

The useful checks are consolidated into `.github/workflows/ci.yml`; obsolete
`.github/workflows/linter.yml` remains deleted. The workflow runs for pull
requests, pushes to `main`, and `workflow_dispatch`, uses `ubuntu-24.04`,
read-only default permissions, concurrency cancellation, explicit timeouts,
full action commit SHAs with release comments, and sanitized failure artifacts.
It has no deployment, `pull_request_target`, repository-secret use, artifact
execution, auto-merge, or write permission.

### Old-to-new workflow and check mapping

| Old workflow/job | New stable required job |
| --- | --- |
| `CI / Server` | `frappe-tests` |
| `Linters / Frappe Linter` | `repository-quality`, `semgrep` |
| `Linters / Vulnerable Dependency Check` | `dependency-vulnerability-audit` |
| No equivalent | `repository-hygiene-and-secrets` |
| No equivalent | `fastapi-unit-tests (<service>)` |
| No equivalent | `python314-runtime-compatibility (<service>)` |
| No equivalent | `compose-validation` |
| No equivalent | `infrastructure-validation` |
| No equivalent | `CI / Required Gate` |

Replace the unstable old required-check names with the one branch-protection
check `CI / Required Gate`. It depends on every matrix and non-matrix required
job and rejects failure, cancellation, or skip.

## Exact runtime and framework choices

- Python: `3.14.6`; root metadata: `>=3.14,<3.15`.
- Frappe: official stable tag `v16.27.1`, commit
  `f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`.
- Frappe Bench: release `5.31.0`, upstream tag commit
  `f21f11793872705560e710b2bda69934c9c34011`.
- Node: `24.18.0`.
- Python base: `python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30`.
- CI MariaDB: `mariadb:11.8.5@sha256:345fa26d595e8c7fe298e0c4098ed400356f502458769c8902229b3437d6da2b`.
- CI Redis: `redis:7.4.5-alpine@sha256:bb186d083732f669da90be8b0f975a37812b15e913465bb14d845db72a4e3e08`.
- Compose external images and independently verified manifest digests are
  recorded in `ci/image-manifest-evidence.txt`.

`ci/verify-upstream-pins.sh` checks the official Frappe/Bench repositories,
annotated tags, commits, Frappe Python range, Node metadata, Bench version, and
Bench Python metadata. Frappe support is an official stable release, not a
development commit. CI additionally checks
`git -C <bench>/apps/frappe rev-parse HEAD` before site creation.

## FastAPI dependencies and Dockerfiles

Every service retains a complete, exact direct production requirement file and
a generated `--require-hashes` lock. Test sources/locks are separate. Every
service Dockerfile uses the exact immutable Python 3.14.6 base and installs the
production lock followed by `pip check`; required FFmpeg/system libraries were
preserved.

The v1 compatibility choices remain: FastAPI `0.139.2`, Uvicorn `0.51.0`,
Pydantic `2.13.4`, Requests `2.34.2`, Redis `7.4.1`, RQ `2.10.0`, Pillow
`12.3.0`; background removal uses rembg `2.0.77`, ONNX Runtime `1.27.0`, Numba
`0.66.0`, NumPy `2.4.6`; image search uses Torch `2.13.0`, OpenCLIP `3.3.0`,
Qdrant client `1.18.0`, NumPy `2.5.1`; translation uses CTranslate2 `4.8.1`,
Transformers `4.57.6`, and SentencePiece `0.2.2`. These are resolver outcomes,
not locally proven installations in v2; the mandatory runtime matrix remains
`NOT RUN`.

## Tests added and coverage policy

| Service | Static test functions | Enforced line floor | Applicable coverage |
| --- | ---: | ---: | --- |
| analytics-pipeline | 9 | 55% | Config, health, signatures, schemas, queue accept/reject, worker success/failure |
| background-removal | 6 | 45% | Config/size bounds, health, invalid uploads, processor success/failure |
| image-search | 6 | 35% | Config/limits, health, invalid uploads, vector/service success/failure |
| moderation | 9 | 55% | Config, health, signatures, schemas, queue accept/reject, worker success/failure |
| notification-delivery | 9 | 40% | Config, health, signatures, schemas, queue accept/reject, worker success/failure |
| search-ranking | 9 | 40% | Config/bounds, health, signatures, schemas, queue accept/reject, worker success/failure |
| translation | 8 | 55% | Config/text bounds, health, validation, translator success/failure |
| video-processing | 9 | 40% | Config/bounds, health, signatures, schemas, queue accept/reject, worker success/failure |

All service test packages prevent outbound sockets. Redis, storage, HTTP,
provider, ML, and subprocess interactions are replaced only at their genuine
external boundaries. Background removal, image search, and translation do not
have queue/HMAC contracts, so no artificial queue/auth behavior was added.
The counts above are static source counts; because pytest could not be
installed, they are not reported as collected or passing and actual coverage
is unavailable.

## Retained behavioral source changes

Only these existing production behaviors differ from the authoritative source:

- `infra/analytics-pipeline/app/main.py`
- `infra/moderation/app/main.py`
- `infra/notification-delivery/app/main.py`
- `infra/search-ranking/app/main.py`
- `infra/video-processing/app/main.py`

Those five signed raw-body APIs convert Pydantic `ValidationError` into a
sanitized HTTP 422 response rather than an unhandled exception. This is a
proven request-validation bug fix needed by the baseline tests; it does not
weaken authentication.

- `infra/translation/app/translator.py` lazily imports CTranslate2 and
  Transformers inside `load()`, allowing Python 3.14 import/health tests
  without initializing or downloading a model. Production loading behavior is
  preserved.
- `infra/notification-delivery/firebase-service-account.example.json` uses a
  scanner-safe, obviously synthetic single-line placeholder instead of a
  private-key-shaped fixture.

No AOS Frappe application behavior change is retained. The seven files
`aos/api/accounts/constants.py`, `aos/api/calls/constants.py`,
`aos/api/catalog/constants.py`, `aos/api/notifications/constants.py`,
`aos/api/verification/constants.py`, `aos/hooks.py`, and
`aos/tests/test_api_versioning.py` differ from the authoritative upload only by
a final newline.

## Dependency locking and security decisions

- `uv 0.11.28` generates root, CI, production, and test locks with hashes;
  direct definitions are exact and test tools are not in production files.
- detect-secrets `1.5.0` runs with hexadecimal/base64 entropy enabled. The
  reviewed baseline contains hashes only. New, stale, or unreviewed findings
  fail without printing suspected values.
- Every tracked dependency lock is separately rejected if it contains a
  credential-bearing URL, token query parameter, or embedded token pattern.
- pip-audit `2.10.1` covers the root, CI/development, and every service
  production lock. There is no ignore/allowlist.
- Semgrep `1.170.0` uses Frappe rules commit
  `3011799f0ed91be48dc70e8e2a480142e106df7e` and community rules commit
  `e5b5a42ec061854378c11e0d01f19250b52bc2e9`; metrics and external upload are
  disabled and ERROR findings block.
- `ci/validate_actions.py` rejects job permission escalation and proves gate
  coverage/result handling.
- Photon was removed from maintained Compose instead of retaining a sentinel
  digest. Its source build verifies Photon 1.2.0 jar SHA-256
  `3455a6c2c9828393c2506d23540015b3b220cf00f4a9bb2c39e8007971cbe8c7`.
  It may return only after an image is published and its real registry digest
  is independently verified.
- Compose `5.3.1`, crane `0.21.7`, ShellCheck `0.11.0`, and source-built Nginx
  `1.28.0` are acquired by version/checksum/commit-pinned local installers.

## Known limitations and blocked gates

- PyPI metadata resolved, but repeated pip and uv wheel transfers from
  `files.pythonhosted.org` timed out. Therefore Semgrep, pip-audit, all eight
  service pytest/coverage runs, all eight clean production installations,
  their `pip check`/entry-point imports, and the full Bench/Frappe installation
  and suite did not run.
- The workflow has not been pushed; there is no GitHub Actions run URL/status.
- Static Frappe collection found 184 methods/functions in 34 files, but this is
  not a runtime test count and does not substitute for `bench run-tests`.
- Unit tests deliberately exclude model inference and live provider/storage
  integration.
- Out-of-scope security findings from v1 remain deferred; this correction does
  not expand into SSRF, internal authentication, replay prevention, analytics
  identity, media authorization, secret management, or later checkpoints.

No existing test was deleted, skipped, weakened, or replaced. `NOT RUN` is an
environmental execution result, not a code-level skip. This checkpoint must
remain **NOT READY** until every mandatory job succeeds in a real run.

## Breaking changes

- Python 3.12/3.13 are unsupported; all runtimes are Python 3.14.6 and metadata
  is `>=3.14,<3.15`.
- Dependencies and images are exact/immutable; updates require lock/digest
  regeneration and review.
- Old workflow check names are replaced by `CI / Required Gate`.
- Photon is absent from maintained Compose until a real digest exists.
- The five malformed signed-request paths now return sanitized 422 responses.

## Manual GitHub repository settings required

1. Push this exact source and let every matrix/non-matrix job finish.
2. Do not approve release while any job is failed, cancelled, skipped, or not
   run.
3. Require the exact check `CI / Required Gate` on `main`.
4. Require pull requests, approvals, conversation resolution, and a current
   branch; dismiss stale approvals.
5. Prevent force pushes/deletion and restrict bypasses.
6. Keep Dependabot security updates visible; do not auto-merge or deploy them.
7. Review uploaded failure artifacts and keep all write/deployment permissions
   out of Checkpoint 1.

The recommended primary branch-protection check is exactly
`CI / Required Gate`. Checkpoint 2 must remain a separate reviewed change and
must not weaken this gate.
