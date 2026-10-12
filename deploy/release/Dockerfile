# RELEASE-1 candidate: not a network-ready service or publicly distributable artifact.
# BASE_IMAGE is checked against qualification/release-image-v1.json by CI; no floating tag.
ARG BASE_IMAGE
FROM --platform=linux/amd64 ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 \
    PYTHONPATH=/opt/bridge:/opt/release-tools \
    PATH=/opt/online3b/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Bootstrap only the trust anchors via Ubuntu's GPG-signed native HTTP APT
# archive. The minimal pinned Ubuntu base lacks CA roots, so HTTPS snapshot
# access cannot work until ca-certificates is installed. The bootstrap's
# package version is independently recorded; distribution remains prohibited
# until this bootstrap dependency is reviewed/locked. Never disable TLS
# verification and never use trusted=yes or allow-unauthenticated.
COPY deploy/release/bootstrap.sources /etc/apt/sources.list.d/bootstrap.sources
RUN set -eu; \
    rm -f /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list; \
    apt-get update -o APT::Update::Error-Mode=any; \
    apt-get install -y --no-install-recommends ca-certificates; \
    test -s /etc/ssl/certs/ca-certificates.crt; \
    rm -f /etc/apt/sources.list.d/bootstrap.sources; \
    rm -rf /var/lib/apt/lists/*
# Once CA trust has been established, ALL remaining APT inputs use the
# fixed signed HTTPS Ubuntu snapshot; no bootstrap source survives.
COPY deploy/release/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources
RUN apt-get update -o APT::Update::Error-Mode=any && \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates procps \
        libopenblas0-pthread libprotobuf32t64 libstdc++6 libgomp1 zlib1g && \
    rm -rf /var/lib/apt/lists/*

COPY build/release1-stage/allfather/ /opt/allfather/
COPY build/release1-stage/bridge/ /opt/bridge/
COPY build/release1-stage/tools/ /opt/release-tools/
COPY build/release1-stage/wheels/ /opt/allfather-release/wheels/
COPY build/release1-stage/manifest.json /opt/allfather-release/manifest.json

RUN python3 -m venv /opt/online3b && \
    /opt/online3b/bin/python -m pip install \
      --no-index --find-links=/opt/allfather-release/wheels \
      --require-hashes -r /opt/allfather-release/wheels/runtime-wheels.lock && \
    dpkg-query -W -f='${Package}=${Version}\n' \
      | LC_ALL=C sort > /opt/allfather-release/os-packages.txt && \
    /opt/online3b/bin/python -m pip freeze --all \
      > /opt/allfather-release/python-packages.txt && \
    useradd --system --uid 10001 --home-dir /nonexistent \
      --shell /usr/sbin/nologin allfather && \
    mkdir -p /var/lib/allfather /run/allfather /tmp /opt/allfather/build/replays-online-hybrid-v2 && \
    chown 10001:10001 /var/lib/allfather /run/allfather /tmp /opt/allfather/build/replays-online-hybrid-v2 && \
    chmod 755 /opt/allfather/deploy/bin/allfather-online

RUN /opt/online3b/bin/python /opt/release-tools/scripts/verify-release-candidate.py --installed

WORKDIR /opt/allfather
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/online3b/bin/python", "/opt/release-tools/deploy/release/entrypoint.py"]
CMD ["verify"]
