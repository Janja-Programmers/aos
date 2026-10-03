#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -ge 1 && $# -le 2 ]] || {
  printf 'usage: verify-promotion-source.sh FULL_RELEASE_SHA [--release-run]\n' >&2
  exit 2
}
sha="$1"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Promotion requires a full Git SHA.' >&2; exit 1; }
[[ "${GITHUB_REPOSITORY:-}" == "Janja-Programmers/aos" ]] || {
  echo 'Image promotion is restricted to the authoritative repository.' >&2
  exit 1
}
[[ "${GITHUB_REF:-}" == "refs/heads/main" ]] || {
  echo 'Image promotion must execute from the reviewed main workflow.' >&2
  exit 1
}
[[ "$(git rev-parse HEAD)" == "$sha" ]] || {
  echo 'Promotion checkout does not match the requested release.' >&2
  exit 1
}
[[ "$(git ls-remote --exit-code origin refs/heads/main | cut -f1)" == "$sha" ]] || {
  echo 'Promotion is stale: requested release is no longer main HEAD.' >&2
  exit 1
}
command -v gh >/dev/null
runs="$(gh api -X GET "repos/${GITHUB_REPOSITORY}/actions/workflows/ci.yml/runs?head_sha=${sha}&event=push&branch=main&per_page=10")"
printf '%s' "$runs" | python3 -c '
import json
import sys

sha, repo = sys.argv[1:]
runs = json.load(sys.stdin).get("workflow_runs") or []
if not any(
    run.get("head_sha") == sha
    and run.get("event") == "push"
    and run.get("head_branch") == "main"
    and run.get("conclusion") == "success"
    and (run.get("head_repository") or {}).get("full_name") == repo
    for run in runs
):
    raise SystemExit("The exact same-repository main push did not pass required CI.")
' "$sha" "$GITHUB_REPOSITORY"

if [[ "${2:-}" == "--release-run" ]]; then
  deployment_runs="$(gh api -X GET "repos/${GITHUB_REPOSITORY}/actions/workflows/deploy.yml/runs?head_sha=${sha}&per_page=20")"
  release_id="$(printf '%s' "$deployment_runs" | python3 -c '
import json
import sys

sha, repo = sys.argv[1:]
runs = json.load(sys.stdin).get("workflow_runs") or []
matching = [
    run for run in runs
    if run.get("head_sha") == sha
    and run.get("event") == "workflow_run"
    and run.get("head_branch") == "main"
    and (run.get("head_repository") or {}).get("full_name") == repo
]
if not matching:
    raise SystemExit("No controlled-deployment release run exists for this commit.")
print(max(matching, key=lambda run: int(run["id"]))["id"])
' "$sha" "$GITHUB_REPOSITORY")"
  artifacts="$(gh api -X GET "repos/${GITHUB_REPOSITORY}/actions/runs/${release_id}/artifacts?per_page=100")"
  printf '%s' "$artifacts" | python3 -c '
import json
import sys

expected = "release-" + sys.argv[1]
artifacts = json.load(sys.stdin).get("artifacts") or []
matches = [
    artifact for artifact in artifacts
    if artifact.get("name") == expected and not artifact.get("expired")
]
if len(matches) != 1:
    raise SystemExit("Exact successful release artifact is absent, expired, or ambiguous.")
' "$sha"
  printf '%s\n' "$release_id"
fi
