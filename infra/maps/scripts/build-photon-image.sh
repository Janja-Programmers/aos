#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ROOT_DIR}/.env"
MANIFEST_FILE="${MAP_MANIFEST_FILE:-${ROOT_DIR}/infra/maps/manifest.env}"

# Read only the build keys we need from the Docker Compose .env file.
# Compose .env syntax is not identical to shell syntax (for example, values
# may contain unquoted spaces), so never source the deployment .env here.
read_dotenv_value() {
    local file="$1" key="$2" line value

    line="$(grep -E "^${key}=" "${file}" | tail -n 1 || true)"
    [[ -n "${line}" ]] || return 1

    value="${line#*=}"
    value="${value%$'\r'}"

    # Remove matching outer quotes, if present.
    if [[ ${#value} -ge 2 && "${value}" == \"*\" && "${value}" == *\" ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value}" == \'*\' && "${value}" == *\' ]]; then
        value="${value:1:${#value}-2}"
    fi

    printf '%s' "${value}"
}

load_dotenv_key() {
    local key="$1" value

    if value="$(read_dotenv_value "${ENV_FILE}" "${key}")"; then
        export "${key}=${value}"
    fi
}

# Load only the explicitly permitted build configuration.
if [[ -f "${ENV_FILE}" ]]; then
    load_dotenv_key PHOTON_VERSION
    load_dotenv_key PHOTON_IMAGE
    load_dotenv_key PHOTON_JAR_SHA256
fi

# Load the trusted manifest second, when present.
# Local development environments may not have this manifest.
#
# Unlike the deployment .env, this file is intentionally sourced and must
# therefore remain a trusted, source-controlled shell configuration file.
if [[ -f "${MANIFEST_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${MANIFEST_FILE}"
    set +a
fi

# Canonical Photon build defaults.
PHOTON_VERSION="${PHOTON_VERSION:-1.2.0}"
PHOTON_IMAGE="${PHOTON_IMAGE:-aos-photon:1.2.0}"
PHOTON_JAR_SHA256="${PHOTON_JAR_SHA256:-3455a6c2c9828393c2506d23540015b3b220cf00f4a9bb2c39e8007971cbe8c7}"

DOCKERFILE="${ROOT_DIR}/infra/maps/photon/Dockerfile"
CONTEXT_DIR="${ROOT_DIR}/infra/maps/photon"

# Validate required build files.
if [[ ! -f "${DOCKERFILE}" ]]; then
    echo "Missing Dockerfile: ${DOCKERFILE}" >&2
    exit 1
fi

if [[ ! -f "${CONTEXT_DIR}/entrypoint.sh" ]]; then
    echo "Missing entrypoint: ${CONTEXT_DIR}/entrypoint.sh" >&2
    exit 1
fi

# Require an exact Photon semantic version.
# Prevent malformed versions from reaching the download URL.
if ! [[ "${PHOTON_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "PHOTON_VERSION must be a semantic version (e.g. 1.2.0)." >&2
    exit 1
fi

# Require the local image tag to match the Photon version exactly.
# This prevents accidentally publishing a JAR under the wrong version tag.
if [[ "${PHOTON_IMAGE}" != "aos-photon:${PHOTON_VERSION}" ]]; then
    echo "PHOTON_IMAGE must be aos-photon:${PHOTON_VERSION}." >&2
    exit 1
fi

# Require a complete SHA-256 checksum.
# The Dockerfile must independently verify the downloaded JAR against
# this checksum during the image build.
if ! [[ "${PHOTON_JAR_SHA256}" =~ ^[[:xdigit:]]{64}$ ]]; then
    echo "PHOTON_JAR_SHA256 must be a 64-character hexadecimal checksum." >&2
    exit 1
fi

echo "Building Photon image"
echo "  image:   ${PHOTON_IMAGE}"
echo "  version: ${PHOTON_VERSION}"
echo

# Build the pinned Photon image.
docker build \
    --build-arg "PHOTON_VERSION=${PHOTON_VERSION}" \
    --build-arg "PHOTON_JAR_URL=https://github.com/komoot/photon/releases/download/${PHOTON_VERSION}/photon-${PHOTON_VERSION}.jar" \
    --build-arg "PHOTON_JAR_SHA256=${PHOTON_JAR_SHA256}" \
    -t "${PHOTON_IMAGE}" \
    -f "${DOCKERFILE}" \
    "${CONTEXT_DIR}"

echo
echo "Photon image built successfully:"

docker image ls "${PHOTON_IMAGE%%:*}" \
    --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"

echo
echo "Verifying Java and Photon JAR inside image..."

# Verify that the built image contains an executable Java runtime
# and a non-empty Photon JAR.
docker run --rm --entrypoint sh "${PHOTON_IMAGE}" -c '
    set -eu
    java -version
    test -s /opt/photon/photon.jar
    ls -lh /opt/photon/photon.jar
'

echo
echo "OK: ${PHOTON_IMAGE} is ready."
