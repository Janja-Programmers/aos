#!/usr/bin/env bash
set -Eeuo pipefail

destination="$1"
repository="$2"
revision="$3"

[[ "${revision}" =~ ^[0-9a-f]{40}$ ]] || {
    printf 'Rule revision must be a full commit SHA.\n' >&2
    exit 1
}
mkdir -p "${destination}"
git -C "${destination}" init --quiet
git -C "${destination}" remote add origin "${repository}"
git -C "${destination}" fetch --quiet --depth 1 origin "${revision}"
git -C "${destination}" checkout --quiet --detach FETCH_HEAD
actual="$(git -C "${destination}" rev-parse HEAD)"
[[ "${actual}" == "${revision}" ]] || {
    printf 'Rule revision mismatch.\n' >&2
    exit 1
}
