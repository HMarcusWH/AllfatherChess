# RELEASE-1: offline-only, non-publishable combined application candidate.
# BASE_IMAGE is never inferred from a moving tag; validate it against source policy.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG TARGETPLATFORM
ARG SOURCE_SHA
LABEL org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.source="https://github.com/HMarcusWH/AllfatherChess" \
      org.opencontainers.image.title="AllfatherChess RELEASE-1 OFFLINE ONLY"
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY deploy/release/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources
RUN apt-get update -o APT::Update::Error-Mode=any && \
    apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ca-certificates procps git \
      libopenblas0-pthread libprotobuf32t64 libstdc++6 libgomp1 zlib1g && \
    rm -rf /var/lib/apt/lists/*
COPY build/release1-stage/wheelhouse/ /opt/release/wheelhouse/
RUN python3 -m venv /opt/online3b && \
    /opt/online3b/bin/python -m pip install --no-index --require-hashes \
       --find-links=/opt/release/wheelhouse \
       -r /opt/release/wheelhouse/runtime-wheels.lock && \
    useradd --uid 10001 --create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin allfather && \
    install -d -m 0700 -o 10001 -g 10001 \
      /var/lib/allfather /run/allfather
COPY build/release1-stage/engine/ /opt/allfather/
COPY build/release1-stage/bridge/ /opt/bridge/
COPY build/release1-stage/combined-manifest.json /opt/release/combined-manifest.json
COPY deploy/release/entrypoint.py /opt/release/entrypoint.py
COPY scripts/verify-release-candidate.py /opt/release/verify_release.py
RUN install -d -m 0700 -o 10001 -g 10001 \
      /opt/allfather/build/replays-online-hybrid-v2 && \
    chmod -R a-w /opt/allfather /opt/bridge /opt/release && \
    chmod 0755 /opt/allfather/deploy/bin/allfather-online
WORKDIR /opt/bridge
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/online3b/bin/python", "/opt/release/entrypoint.py"]
CMD ["verify"]
