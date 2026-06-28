# AOS Photon Geocoder Image
#
# This image packages the upstream Komoot Photon geocoder release jar.
#
# Build locally as:
#   aos-photon:1.2.0
#
# Do not confuse this with Docker Hub's official `photon` image, which is
# VMware Photon OS and not the Photon geocoder.

FROM eclipse-temurin:21-jre-jammy

ARG PHOTON_VERSION=1.2.0
ARG PHOTON_JAR_URL=https://github.com/komoot/photon/releases/download/1.2.0/photon-1.2.0.jar

LABEL org.opencontainers.image.title="AOS Photon Geocoder"
LABEL org.opencontainers.image.description="AOS packaged image for Komoot Photon geocoder"
LABEL org.opencontainers.image.version="${PHOTON_VERSION}"
LABEL org.opencontainers.image.source="https://github.com/komoot/photon"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV PHOTON_VERSION="${PHOTON_VERSION}" \
    PHOTON_HOME="/opt/photon" \
    PHOTON_DATA_DIR="/photon/photon_data" \
    PHOTON_LISTEN_IP="0.0.0.0" \
    PHOTON_PORT="2322" \
    PHOTON_MAX_RESULTS="20" \
    PHOTON_MAX_REVERSE_RESULTS="10" \
    PHOTON_QUERY_TIMEOUT_SECONDS="5" \
    PHOTON_DEFAULT_LANGUAGE="en" \
    JAVA_OPTS="-Xms512m -Xmx2g"

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    groupadd --system --gid 10001 photon; \
    useradd --system --uid 10001 --gid photon --home-dir /photon --shell /usr/sbin/nologin photon; \
    mkdir -p "${PHOTON_HOME}" "${PHOTON_DATA_DIR}"; \
    chown -R photon:photon "${PHOTON_HOME}" /photon

RUN set -eux; \
    curl -fsSL "${PHOTON_JAR_URL}" -o "${PHOTON_HOME}/photon.jar"; \
    test -s "${PHOTON_HOME}/photon.jar"; \
    chown photon:photon "${PHOTON_HOME}/photon.jar"

COPY entrypoint.sh /usr/local/bin/aos-photon-entrypoint

RUN set -eux; \
    chmod 0755 /usr/local/bin/aos-photon-entrypoint

WORKDIR /photon/photon_data

USER photon

EXPOSE 2322

VOLUME ["/photon/photon_data"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/aos-photon-entrypoint"]

CMD ["serve"]
