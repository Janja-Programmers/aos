#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
load_manifest
require_command docker

: "${PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES:?Point this job at the INACTIVE/blue-green OpenSearch target}"
: "${PHOTON_IMPORT_CONFIRM_TARGET:?Set PHOTON_IMPORT_CONFIRM_TARGET=YES after verifying the target is not serving production}"
[[ "${PHOTON_IMPORT_CONFIRM_TARGET}" == "YES" ]] || fail "Refusing destructive Photon import without explicit target confirmation"

ensure_image "${PHOTON_IMAGE}"
mode="${1:---from-dump}"
common=(--rm --network host --security-opt no-new-privileges:true \
    -e "PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES=${PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES}" \
    -e "PHOTON_OPENSEARCH_CLUSTER=${PHOTON_OPENSEARCH_CLUSTER:-photon}" \
    -e "JAVA_OPTS=${PHOTON_IMPORT_JAVA_OPTS:--Xms2g -Xmx8g -XX:+ExitOnOutOfMemoryError}" \
    "${PHOTON_IMAGE}")

if [[ "${mode}" == "--from-dump" ]]; then
    require_command zstd
    dump="${PHOTON_DUMP_LOCAL_PATH:-}"
    if [[ -z "${dump}" ]]; then
        : "${PHOTON_DUMP_URL:?PHOTON_DUMP_URL or PHOTON_DUMP_LOCAL_PATH is required}"
        : "${PHOTON_DUMP_SHA256:?PHOTON_DUMP_SHA256 is required}"
        validate_sha256 "${PHOTON_DUMP_SHA256}" PHOTON_DUMP_SHA256
        require_command curl
        mkdir -p "${ROOT_DIR}/maps/downloads"
        dump="${ROOT_DIR}/maps/downloads/${PHOTON_DUMP_FILENAME:-photon-planet.jsonl.zst}"
        tmp="${dump}.partial"
        curl --fail --location --retry 5 --retry-all-errors --output "${tmp}" "${PHOTON_DUMP_URL}"
        [[ "$(sha256_file "${tmp}")" == "${PHOTON_DUMP_SHA256,,}" ]] || fail "Photon dump checksum mismatch"
        mv -f "${tmp}" "${dump}"
    fi
    assert_nonempty_file "${dump}"
    if [[ -n "${PHOTON_DUMP_SHA256:-}" && "${PHOTON_DUMP_SHA256}" != REPLACE_* ]]; then
        [[ "$(sha256_file "${dump}")" == "${PHOTON_DUMP_SHA256,,}" ]] || fail "Photon dump checksum mismatch"
    fi
    info "Importing Photon dump into inactive OpenSearch target"
    zstd --stdout -d "${dump}" | docker run -i "${common[@]}" import -import-file -
elif [[ "${mode}" == "--from-nominatim" ]]; then
    : "${PHOTON_IMPORT_DB_HOST:?PHOTON_IMPORT_DB_HOST is required}"
    : "${PHOTON_IMPORT_DB_USER:?PHOTON_IMPORT_DB_USER is required}"
    : "${PHOTON_IMPORT_DB_PASSWORD:?PHOTON_IMPORT_DB_PASSWORD is required}"
    docker run "${common[@]}" import \
        -host "${PHOTON_IMPORT_DB_HOST}" \
        -port "${PHOTON_IMPORT_DB_PORT:-5432}" \
        -database "${PHOTON_IMPORT_DB_NAME:-nominatim}" \
        -user "${PHOTON_IMPORT_DB_USER}" \
        -password "${PHOTON_IMPORT_DB_PASSWORD}" \
        -languages "${PHOTON_IMPORT_LANGUAGES:-en,fr,es,pt,ar,sw}"
else
    fail "Usage: $0 --from-dump|--from-nominatim"
fi
info "Photon import completed. Verify with a temporary Photon replica before flipping runtime endpoints."
