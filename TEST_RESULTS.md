# Checkpoint 1 correction test results

Execution date: 2026-07-18  
Environment: local Linux x86-64 sandbox  
Python: `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14`
reported `3.14.6`  
GitHub Actions: `NOT RUN` — the correction source was not pushed and no
repository/run URL was available.

## Readiness result

**NOT READY.** Only commands that completed successfully are marked `PASS`.
Configuration, source collection, or a workflow definition is not substituted
for runtime execution. Package downloads from `files.pythonhosted.org` timed
out on repeated pip and uv-backed attempts, so the package-dependent mandatory
gates below remain `NOT RUN`.

## Repository, policy, and source checks

| Check | Result | Exact command and observed result |
| --- | --- | --- |
| Python version | PASS | `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 -c 'import platform; print(platform.python_version())'` → `3.14.6`, exit 0. |
| Python compilation | PASS | `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 -m compileall -q aos infra ci` → exit 0. |
| Ruff lint | PASS | `/tmp/aos-cp1-quality/bin/ruff check ci infra/*/tests` → `All checks passed!`, exit 0. Scope is intentionally limited to Checkpoint-owned CI/test code so legacy AOS source is not reformatted. |
| Ruff format check | PASS | `/tmp/aos-cp1-quality/bin/ruff format --check ci infra/*/tests` → `42 files already formatted`, exit 0. |
| Pre-commit | PASS | `PATH=/tmp/aos-cp1-quality/bin:$PATH PRE_COMMIT_HOME=/tmp/aos-v2-precommit pre-commit run --all-files` → all non-mutating hooks passed, exit 0. |
| Repository validator | PASS | `/tmp/aos-cp1-quality/bin/python ci/validate_repository.py .` → JSON, TOML, YAML, duplicate-key, conflict, debug, and configured whitespace checks OK, exit 0. |
| Workflow security policy | PASS | `/tmp/aos-cp1-quality/bin/python ci/validate_actions.py` → action pins, job/workflow permissions, runner, timeout, and fail-closed gate coverage OK, exit 0. |
| Foundation/lock policy | PASS | `/tmp/aos-cp1-quality/bin/python ci/validate_foundation.py` → version manifest, source/lock pairs, hashes, Dockerfiles, and service matrices OK, exit 0. |
| Repository hygiene | PASS | `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 ci/repository_hygiene.py` → every final tracked path satisfied the prohibited-artifact policy, exit 0. |
| Lock credential policy | PASS | `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 ci/validate_lock_credentials.py` → 21 tracked lock files checked, exit 0. |
| Secret scan | PASS | Exact detect-secrets `1.5.0` source tag was run with hexadecimal/base64 entropy enabled, then `/tmp/aos-cp1-quality/bin/python ci/compare_secret_baseline.py .secrets.baseline /tmp/aos-v2-secrets-current-final.json` → 78 reviewed hash-only findings and no new/stale/unreviewed finding, exit 0. Lock URLs/tokens were separately checked by the prior command. |
| Frappe source collection assertion | PASS | `/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 ci/assert_frappe_tests.py` → 184 static test methods/functions in 34 files, exit 0. This is not a runtime test count. |
| Official Frappe/Bench metadata | PASS | `AOS_PYTHON=/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 ci/verify-upstream-pins.sh` verified official Frappe `v16.27.1`/`f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`, Python `>=3.14,<3.15`, Node 24 metadata, and Bench `5.31.0`/`f21f11793872705560e710b2bda69934c9c34011`, exit 0. |

The compile, Ruff, pre-commit, repository, Actions, foundation, hygiene,
secret, lock-credential, and static-collection checks were rerun after the
final source/document correction. Generated caches were removed after these
runs and are excluded from the ZIP.

## Security scanners and dependency audit

| Gate | Result | Exact command / reason |
| --- | --- | --- |
| Semgrep | NOT RUN | `AOS_PYTHON=... AOS_CI_WORKDIR=/tmp/aos-v2-final-semgrep PIP_DEFAULT_TIMEOUT=15 PIP_RETRIES=1 ci/semgrep.sh` exited 1 before Semgrep could start: the hash-locked environment download of `annotated-types==0.7.0` timed out at `files.pythonhosted.org`. Exact Semgrep `1.170.0` and pinned rules are configured, but no scan result is claimed. |
| Dependency vulnerability audit | NOT RUN | `AOS_PYTHON=... AOS_CI_WORKDIR=/tmp/aos-v2-final-audit PIP_DEFAULT_TIMEOUT=15 PIP_RETRIES=1 ci/dependency-audit.sh` exited 1 before pip-audit could start on the same timed-out hash-locked wheel download. No advisory ignore exists and no audit result is claimed. |

## FastAPI unit tests and coverage

Exact interface:

```bash
AOS_PYTHON=/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 \
  ci/run-fastapi-tests.sh <service>
```

The final retry for `analytics-pipeline` used a clean environment at
`/tmp/aos-v2-final-test`, `PIP_DEFAULT_TIMEOUT=15`, and `PIP_RETRIES=1`; it
exited 1 while downloading hash-locked `anyio==4.14.2`. The earlier normal
120-second attempts and uv cache/resolution attempts encountered the same
blocked wheel host. Because the common exact test lock could not install, none
of the final v2 suites collected. The source counts are reported only to prove
test presence and are not passes.

| Service | Result | Static functions | Floor | Actual pytest count / coverage |
| --- | --- | ---: | ---: | --- |
| analytics-pipeline | NOT RUN | 9 | 55% | Not collected; actual coverage unavailable. |
| background-removal | NOT RUN | 6 | 45% | Not collected; actual coverage unavailable. |
| image-search | NOT RUN | 6 | 35% | Not collected; actual coverage unavailable. |
| moderation | NOT RUN | 9 | 55% | Not collected; actual coverage unavailable. |
| notification-delivery | NOT RUN | 9 | 40% | Not collected; actual coverage unavailable. |
| search-ranking | NOT RUN | 9 | 40% | Not collected; actual coverage unavailable. |
| translation | NOT RUN | 8 | 55% | Not collected; actual coverage unavailable. |
| video-processing | NOT RUN | 9 | 40% | Not collected; actual coverage unavailable. |

No JUnit/coverage artifact from these unsuccessful final attempts is included
in the deliverable.

## Python 3.14 production compatibility matrix

Exact interface:

```bash
AOS_PYTHON=/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 \
  ci/check-python314-compatibility.sh <service>
```

The final clean `analytics-pipeline` retry at `/tmp/aos-v2-final-compat` exited
1 while downloading hash-locked `annotated-doc==0.0.4` from the same timing-out
host. Therefore no complete install, `pip check`, or all-entry-point import is
claimed for any service.

| Service | Clean hash install | `pip check` | all app/entry-point imports | Exact reason |
| --- | --- | --- | --- | --- |
| analytics-pipeline | NOT RUN | NOT RUN | NOT RUN | Hash-locked wheel transfer timed out before installation. |
| background-removal | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable; no reduced substitute used. |
| image-search | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable; no ML dependencies removed. |
| moderation | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable. |
| notification-delivery | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable. |
| search-ranking | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable. |
| translation | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable; no model download attempted. |
| video-processing | NOT RUN | NOT RUN | NOT RUN | Common package-download path unavailable; FFmpeg requirements retained. |

## Frappe installation and test suite

| Check | Result | Exact reason |
| --- | --- | --- |
| Official tag/metadata verification | PASS | `ci/verify-upstream-pins.sh` completed as recorded above. |
| Bench `5.31.0` installation | NOT RUN | Hash-locked Python/package installation could not complete. |
| Installed Frappe revision check | NOT RUN | No Bench environment was installed, so `git -C <bench>/apps/frappe rev-parse HEAD` could not execute. |
| Disposable site/migration | NOT RUN | Docker/Bench, MariaDB, and Redis runtimes were unavailable locally. |
| `bench --site <ci-site> run-tests --app aos` | NOT RUN | No disposable site existed. Runtime Frappe test count is unavailable; static 184 is not substituted. |

The workflow provisions digest-pinned MariaDB/Redis and verifies the pinned
Frappe checkout before a fresh site, migration, and full suite, but that
definition is not a local pass.

## Compose validation

Tool installer:

```bash
ci/install-compose-tools.sh /tmp/aos-v2-compose-tools
```

It verified Docker Compose `5.3.1` archive SHA-256
`f9ebc6ebdb19d769b793c245a736caaeb198c62587f13b25c660c13b4987f959`
and crane `0.21.7` archive SHA-256
`1a57bc98207fa1c0d04bf760699099e26f8383499bfd55b99c1b919a928a7230`.

| Check | Result | Exact command/result |
| --- | --- | --- |
| Compose config | PASS | `/tmp/aos-v2-compose-tools/bin/docker-compose --env-file ci/compose.env -f docker-compose.yml config --quiet` → exit 0; no service started. |
| Rendered Compose | PASS | Same command with `config > /tmp/aos-v2-compose-final/rendered.yml` → exit 0. |
| Duplicate-key-aware YAML | PASS | `/tmp/aos-cp1-quality/bin/python ci/validate_repository.py . --yaml-only` → exit 0. |
| Compose policy | PASS | `/tmp/aos-cp1-quality/bin/python ci/compose_policy.py /tmp/aos-v2-compose-final/rendered.yml` → digest, placeholder, loopback, healthcheck, and tag policies OK, exit 0. |
| External image manifests | PASS | `PATH=/tmp/aos-v2-compose-tools/bin:/tmp/aos-cp1-quality/bin:$PATH ci/verify-image-manifests.sh /tmp/aos-v2-compose-final/rendered.yml /tmp/aos-v2-compose-final/evidence.txt` → seven configured Compose manifests matched their immutable digests, exit 0. Separate `crane digest` commands also verified the MariaDB 11.8.5 and Python 3.14.6 base refs; all nine results are recorded in `ci/image-manifest-evidence.txt`. |

## Infrastructure validation

`ci/install-infra-tools.sh /tmp/aos-v2-infra-tools-3` verified ShellCheck
`0.11.0` archive SHA-256
`8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198`
and built official Nginx `release-1.28.0` commit
`481d28cb4e04c8096b9b6134856891dc52ecc68f` in a temporary prefix.

Exact aggregate command:

```bash
AOS_PYTHON=/tmp/uv-python/cpython-3.14.6-linux-x86_64-gnu/bin/python3.14 \
AOS_CI_WORKDIR=/tmp/aos-v2-infra-final \
PATH=/tmp/aos-v2-infra-tools-3/bin:/tmp/aos-cp1-quality/bin:$PATH \
NGINX_MIME_TYPES=/tmp/aos-v2-infra-tools-3/nginx/conf/mime.types \
ci/validate-infrastructure.sh
```

| Check | Result | Observed result |
| --- | --- | --- |
| `bash -n` and executable bits | PASS | Every maintained `ci/*.sh` and `infra/**/*.sh` passed; required scripts were executable. |
| ShellCheck | PASS | ShellCheck 0.11.0 warnings-and-higher policy passed. |
| Nginx rendering | PASS | All maintained templates rendered with synthetic values and no unresolved variable. |
| `nginx -t` | PASS | Nginx 1.28.0 reported syntax OK and successful configuration test against the temporary configuration/certificates. |
| systemd static validation | PASS | Three maintained unit files passed required section/directive validation. |
| documented paths | PASS | 43 documented maintained paths existed. |

Nginx emitted non-fatal deprecation/stapling warnings for the synthetic test
configuration; its exit status was 0. Host Nginx/systemd configuration was not
modified.

## GitHub Actions

`NOT RUN` — no repository access/run URL was available and the workflow was not
pushed. Do not configure a release as ready until a real `CI / Required Gate`
run succeeds.

## Final ZIP integrity

The final archive is created from the repository root without `.git`, caches,
test reports, virtual environments, models, datasets, volumes, backups, or the
70 MB generated glyph PBF set. `ARTIFACTS_MANIFEST.md` documents reproducible
acquisition/build instructions.

- Archive: `aos-foundation-01-ci-v2.zip`
- Embedded packaging-candidate byte size: `1,624,059`
- Embedded packaging-candidate SHA-256: `3d85a6515c561769e6187bfaa33e62de7798f5a3a3fb44be620d1e8a24a7554a`
- `unzip -t`: PASS — `No errors detected in compressed data`.
- Required-member assertion: PASS — 1,099 entries, required files, and all
  eight service directories present; prohibited generated members absent.
- `zipinfo -1`: PASS — completed and recorded 1,099 member paths.

The true delivery SHA-256 is also returned with the final file. An archive
cannot contain its own final cryptographic digest without changing that digest;
the embedded value is therefore the immediately preceding, identically scoped
packaging candidate, while the delivery summary reports the true final bytes.
