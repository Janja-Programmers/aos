#!/usr/bin/env sh
set -eu

PHOTON_HOME="${PHOTON_HOME:-/opt/photon}"
PHOTON_DATA_DIR="${PHOTON_DATA_DIR:-/photon/photon_data}"
PHOTON_LISTEN_IP="${PHOTON_LISTEN_IP:-0.0.0.0}"
PHOTON_PORT="${PHOTON_PORT:-2322}"
PHOTON_MAX_RESULTS="${PHOTON_MAX_RESULTS:-20}"
PHOTON_MAX_REVERSE_RESULTS="${PHOTON_MAX_REVERSE_RESULTS:-10}"
PHOTON_QUERY_TIMEOUT_SECONDS="${PHOTON_QUERY_TIMEOUT_SECONDS:-5}"
PHOTON_DEFAULT_LANGUAGE="${PHOTON_DEFAULT_LANGUAGE:-en}"
JAVA_OPTS="${JAVA_OPTS:--Xms512m -Xmx2g}"

if [ ! -f "${PHOTON_HOME}/photon.jar" ]; then
    echo "Photon jar not found at ${PHOTON_HOME}/photon.jar" >&2
    exit 1
fi

mkdir -p "${PHOTON_DATA_DIR}"

cd "${PHOTON_DATA_DIR}"

if [ "$#" -eq 0 ]; then
    set -- serve
fi

if [ "$1" = "serve" ]; then
    shift

    exec java ${JAVA_OPTS} -jar "${PHOTON_HOME}/photon.jar" serve \
        -listen-ip "${PHOTON_LISTEN_IP}" \
        -listen-port "${PHOTON_PORT}" \
        -data-dir "${PHOTON_DATA_DIR}" \
        -max-results "${PHOTON_MAX_RESULTS}" \
        -max-reverse-results "${PHOTON_MAX_REVERSE_RESULTS}" \
        -query-timeout "${PHOTON_QUERY_TIMEOUT_SECONDS}" \
        -default-language "${PHOTON_DEFAULT_LANGUAGE}" \
        "$@"
fi

exec java ${JAVA_OPTS} -jar "${PHOTON_HOME}/photon.jar" "$@"
