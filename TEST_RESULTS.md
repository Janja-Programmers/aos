# Checkpoint 1 CI validation results

Execution date: 2026-07-18  
Pull request: <https://github.com/Janja-Programmers/aos/pull/3>  
Validated implementation commit: `b321f4e3cb64ca1f79e03a87ef8d0bfa0b9e4547`  
Evidence run: [CI #316](https://github.com/Janja-Programmers/aos/actions/runs/29653725246)  
Runner/runtime: GitHub-hosted `ubuntu-24.04`, CPython `3.14.6`

## Readiness result

**READY — PASS.** The complete `.github/workflows/ci.yml` workflow ran on
GitHub-hosted runners with normal package access. All 24 jobs completed
successfully, including the fail-closed `CI / Required Gate`. No
`continue-on-error`, reduced dependency set, skipped matrix entry, or
package-timeout override was used.

The temporary read-only lock-generation workflow used to refresh the
translation lock was removed before CI #316. It never had repository write
permission and never pushed from GitHub Actions.

## Required job results

| Required job | Result | Observed evidence |
| --- | --- | --- |
| `repository-quality` | PASS | Compilation, Ruff, formatting, pre-commit, workflow policy, repository, foundation, lock, and clean-tree checks passed. |
| `repository-hygiene-and-secrets` | PASS | Tracked-artifact, credential-bearing lock URL, detect-secrets baseline, and working-tree checks passed. |
| `dependency-vulnerability-audit` | PASS | All 21 CI, root, service production, and service test locks were audited. No known unaccepted vulnerability remained. |
| `semgrep` | PASS | 63 pinned rules scanned 600 files with 0 findings. |
| Eight `fastapi-unit-tests` jobs | PASS | All 65 tests passed and every service exceeded its coverage floor. |
| Eight `python314-runtime-compatibility` jobs | PASS | Every complete production lock installed under Python 3.14.6; `pip check` and all application imports passed. |
| `frappe-tests` | PASS | Bench 5.31.0, pinned Frappe commit verification, disposable site creation, AOS installation, migration, and the complete runtime suite passed. |
| `compose-validation` | PASS | Synthetic Compose rendering and all digest, placeholder, healthcheck, and binding policies passed without starting the application stack. |
| `infrastructure-validation` | PASS | Bash, ShellCheck, Nginx render/test, systemd, executable-bit, and documented-path validation passed. |
| `CI / Required Gate` | PASS | Every required `needs` result was exactly `success`. |

## FastAPI unit tests and coverage

| Service | Tests | Coverage | Floor | Result |
| --- | ---: | ---: | ---: | --- |
| analytics-pipeline | 9 | 76% | 55% | PASS |
| background-removal | 6 | 65% | 45% | PASS |
| image-search | 6 | 49% | 35% | PASS |
| moderation | 9 | 66% | 55% | PASS |
| notification-delivery | 9 | 51% | 40% | PASS |
| search-ranking | 9 | 45% | 40% | PASS |
| translation | 8 | 72% | 55% | PASS |
| video-processing | 9 | 66% | 40% | PASS |
| **Total** | **65** | — | — | **PASS** |

## Python 3.14 production compatibility

| Service | Hash-locked install | `pip check` | All application imports |
| --- | --- | --- | --- |
| analytics-pipeline | PASS | PASS | PASS |
| background-removal | PASS | PASS | PASS |
| image-search | PASS | PASS | PASS |
| moderation | PASS | PASS | PASS |
| notification-delivery | PASS | PASS | PASS |
| search-ranking | PASS | PASS | PASS |
| translation | PASS | PASS | PASS |
| video-processing | PASS | PASS | PASS |

The translation production lock resolved `transformers==5.14.1` and passed
both the vulnerability audit and Python 3.14 runtime job.

## Frappe runtime suite

The workflow installed exact Bench `5.31.0`, checked out Frappe
`f33ac3f00ab818e21b25ddbec93efb653fd9aa1b`, asserted that installed Git
revision, created a disposable MariaDB/Redis-backed site, installed AOS,
enabled tests, and migrated successfully.

The runtime output reported three passing phases: 19, 150, and 14 tests
(183 total). The job's non-zero-test assertion and full
`bench --site ci-site run-tests --app aos` command both passed.

## Vulnerability exception status

Semgrep `1.170.0` is the newest available release and hard-pins
`click==8.1.8` and `mcp==1.23.3`. Four exact CI-tool-only advisories are
recorded in `ci/vulnerability-exceptions.json`; the audit reported no known
vulnerabilities with those four reviewed entries ignored.

The exception validator fails on malformed, duplicate, expired, or
lock-mismatched entries. All four entries expire on **2026-08-01** and must be
removed when Semgrep supports Click 8.3.3 and MCP 1.28.1 or newer. The AOS scan
uses local rules, does not call `click.edit()`, and starts no MCP task, SSE,
Streamable HTTP, or WebSocket server.

## Log-proven corrections

CI #305 proved and the final source corrected:

- service imports executed with the script directory instead of each service
  directory on `sys.path`;
- Bench invoked `uv` before it was installed on the active `PATH`;
- the video-processing worker fixture lacked
  `minio_public_base_url`;
- six Frappe moderation fields were mutated in `on_update` instead of before
  persistence;
- two location-sort branches used strings as booleans;
- the Firebase optional-module check triggered an identical-comparison rule;
- one internal argv-only subprocess boundary needed a scoped Semgrep rationale;
- the translation lock used a vulnerable Transformers release; and
- Semgrep's newest release had four unfixable, unreachable CI-tool advisories
  requiring short-lived reviewed exceptions.

No backend feature rewrite, frontend compatibility change, deployment,
production secret access, or staging/production mutation was performed.
