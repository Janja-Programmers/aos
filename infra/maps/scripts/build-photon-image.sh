#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ROOT_DIR}/.env"
MANIFEST_FILE="${MAP_MANIFEST_FILE:-${ROOT_DIR}/infra/maps/manifest.env}"

# Load .env first because deployment/runtime values live there on staging.
if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
fi

# Load manifest second only when present, because local dev may not have it.
if [[ -f "${MANIFEST_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${MANIFEST_FILE}"
    set +a
fi

PHOTON_VERSION="${PHOTON_VERSION:-1.2.0}"
PHOTON_IMAGE="${PHOTON_IMAGE:-aos-photon:1.2.0}"
PHOTON_JAR_SHA256="${PHOTON_JAR_SHA256:-3455a6c2c9828393c2506d23540015b3b220cf00f4a9bb2c39e8007971cbe8c7}"

DOCKERFILE="${ROOT_DIR}/infra/maps/photon/Dockerfile"
CONTEXT_DIR="${ROOT_DIR}/infra/maps/photon"

if [[ ! -f "${DOCKERFILE}" ]]; then
    echo "Missing Dockerfile: ${DOCKERFILE}" >&2
    exit 1
fi

if [[ ! -f "${CONTEXT_DIR}/entrypoint.sh" ]]; then
    echo "Missing entrypoint: ${CONTEXT_DIR}/entrypoint.sh" >&2
    exit 1
fi

case "${PHOTON_IMAGE}" in
    aos-photon:[0-9]*.[0-9]*.[0-9]*)
        ;;
    *)
        echo "PHOTON_IMAGE must be a local AOS Photon semver tag, for example aos-photon:1.2.0." >&2
        echo "Current value: ${PHOTON_IMAGE}" >&2
        exit 1
        ;;
esac

echo "Building Photon image"
echo "  image:   ${PHOTON_IMAGE}"
echo "  version: ${PHOTON_VERSION}"
echo

docker build \
    --build-arg "PHOTON_VERSION=${PHOTON_VERSION}" \
    --build-arg "PHOTON_JAR_URL=https://github.com/komoot/photon/releases/download/${PHOTON_VERSION}/photon-${PHOTON_VERSION}.jar" \
    --build-arg "PHOTON_JAR_SHA256=${PHOTON_JAR_SHA256}" \
    -t "${PHOTON_IMAGE}" \
    -f "${DOCKERFILE}" \
    "${CONTEXT_DIR}"

echo
echo "Photon image built successfully:"
docker image ls "${PHOTON_IMAGE%%:*}" --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"

echo
echo "Verifying Java and Photon jar inside image..."

docker run --rm --entrypoint sh "${PHOTON_IMAGE}" -c '
    set -eu
    java -version
    test -s /opt/photon/photon.jar
    ls -lh /opt/photon/photon.jar
    test -d /photon/photon_data
'

echo
echo "OK: ${PHOTON_IMAGE} is ready."
