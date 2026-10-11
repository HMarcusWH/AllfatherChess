# Offline UCI artifact only. No credentials, bridge or public game capability.
# Supply an immutable Linux/amd64 BASE_IMAGE ending in @sha256:... at build time.
# Apt packages below are not yet lockfile-qualified; OCI release needs a later gate.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 ca-certificates libopenblas0-pthread libprotobuf32t64 libstdc++6 libgomp1 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY dist/online3a-stage/ /opt/allfather/
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin allfather \
    && mkdir -p /opt/allfather/build/replays-online-hybrid-v2 \
    && chown 10001:10001 /opt/allfather/build/replays-online-hybrid-v2 \
    && chmod 755 /opt/allfather/deploy/bin/allfather-online
WORKDIR /opt/allfather
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/python3", "/opt/allfather/deploy/bin/allfather-online"]
