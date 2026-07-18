# CI/CD foundation — Checkpoint 1

Checkpoint 1 implements CI and test gates only. It does not deploy, connect by
SSH, read production secrets, or change staging/production systems. Deployment
belongs to Checkpoint 2 after secret and production configuration hardening.

## Compatibility baseline

`ci/versions.env` is the execution manifest. The selected framework is the
stable Frappe `v16.27.1` release at full commit
`f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`. Its upstream Python metadata
requires `>=3.14,<3.15`, and its server-test workflow uses Python 3.14, Node 24,
and MariaDB 11.8. AOS pins Python `3.14.6`, Node `24.18.0`, Bench `5.31.0`,
MariaDB `11.8.5`, and Redis `7.4.5-alpine`. CI images include reviewed
multi-architecture SHA-256 manifest digests.

This is stable framework support, not an unreleased development commit. Bench
is installed in an isolated tool environment because Bench 5.31.0 and Frappe
have different Click constraints; the Frappe virtual environment is resolved
independently. CI verifies the installed Frappe Git revision before creating a
site.

Upstream references:

- Frappe release: <https://github.com/frappe/frappe/releases/tag/v16.27.1>
- Pinned Frappe Python metadata: <https://github.com/frappe/frappe/blob/f33ac3f00ab818e21b25ddbec93efb653fd9aa1b/pyproject.toml>
- Pinned Frappe server tests: <https://github.com/frappe/frappe/blob/f33ac3f00ab818e21b25ddbec93efb653fd9aa1b/.github/workflows/server-tests.yml>
- Bench 5.31.0: <https://pypi.org/project/frappe-bench/5.31.0/>

## Required jobs

| Stable check | Purpose |
| --- | --- |
| `repository-quality` | Compile, Ruff lint/format check, pre-commit, data-format/duplicate-key/conflict/debug/whitespace validation, clean-tree assertion |
| `repository-hygiene-and-secrets` | Tracked-artifact policy, reviewed detect-secrets baseline with entropy enabled, and lock credential-URL policy |
| `dependency-vulnerability-audit` | pip-audit for root, all CI locks, and all eight service production locks |
| `semgrep` | Exact Semgrep plus exact Frappe/community rule commits, local scanning only |
| `fastapi-unit-tests (<service>)` | Eight isolated, network-blocked unit-test entries with JUnit and coverage XML |
| `python314-runtime-compatibility (<service>)` | Eight complete production installations, `pip check`, and entry-point imports |
| `frappe-tests` | Exact Bench/Frappe, digest-pinned disposable MariaDB/Redis, fresh site, migration, full AOS suite |
| `compose-validation` | Duplicate-aware parsing, `docker compose config`, digest/healthcheck/binding policies; no services started |
| `infrastructure-validation` | Bash, ShellCheck, temporary Nginx render/test, systemd syntax, executable bits, documented paths |
| `CI / Required Gate` | Fail-closed aggregation of every job above |

Use `CI / Required Gate` as the primary branch-protection check. It evaluates
all `needs` results under `always()` and fails for failure, cancellation, or an
unexpected skip. Do not configure individual matrix expansions as the only
required checks because service additions change those names.

Recommended branch settings for `main`:

- Require a pull request and at least one approving review.
- Dismiss stale approvals after changes.
- Require conversation resolution and a current branch.
- Require `CI / Required Gate` to pass.
- Prevent bypasses and force pushes; restrict deletion.
- Do not enable automatic dependency-update merges.

No write permission is granted to pull-request jobs. The workflow never uses
`pull_request_target`, does not consume repository secrets, pins every action
to a full commit SHA, fixes the runner to `ubuntu-24.04`, applies explicit
timeouts, and cancels superseded runs.

`ci/validate_actions.py` also rejects job-level permission escalation. A job
may inherit the read-only workflow default or explicitly request only
`contents: read`; write access (including `id-token`, `actions`, `packages`, or
`security-events`) fails validation. The validator also proves the aggregate
gate depends on every other job and checks every `needs` result for exact
`success` under `always()`.

## Updating pins safely

For an Action update, review the upstream release and source diff, resolve its
release tag to a full 40-character commit, replace the SHA, and update the
same-line version comment. Run `make fast`; the Actions policy rejects mutable
tags or missing comments.

For Frappe, select an official release or reviewed commit whose `pyproject.toml`
still supports `>=3.14,<3.15`. Review its upstream server-test workflow and
Node/database matrix, update `FRAPPE_REF`, `FRAPPE_RELEASE`, and compatible
image pins, then run the disposable Frappe job. Never change only the readable
release label: the full commit is authoritative.

Dependabot groups weekly Actions, Python, per-service, CI, and Docker updates.
Every update must pass Python 3.14 and pinned-Frappe compatibility before merge.
Security updates remain visible and are not automatically merged or deployed.

## Findings policy and known limits

Semgrep metrics, version checks, and external uploads are disabled. The CI gate
fails on ERROR findings. pip-audit has no ignore list. A temporary advisory
acceptance would require a reviewed allowlist recording advisory ID, package,
justification, owner, expiration, and removal condition; none exists now.

The secret scan keeps hexadecimal and base64 entropy detection enabled. Its
committed baseline stores reviewed hashes only; new, stale, or unreviewed
findings fail. Because dependency locks contain many package hashes, a separate
validator rejects credential-bearing URLs, query tokens, and embedded token
patterns in every tracked lock.

Unit tests do not download ML models, call providers, or validate real model
inference. Compose validation queries pinned registry manifests using
checksum-pinned crane but does not download layers or start the stack. Photon
is not maintained in Compose until its locally built image is published and a
real registry digest is recorded. Nginx is tested with one-day synthetic
certificates. GitHub-hosted results cannot be claimed until the workflow is
pushed and run; `TEST_RESULTS.md` distinguishes local results from GitHub
status.
