# RELEASE-2B-2: offline-only candidate. Not a public-release approval.
# One actual native/Python dependency installer, inherited by the application.
ARG BASE_IMAGE
FROM --platform=linux/amd64 docker.io/library/python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS candidate-transport-ca
FROM --platform=linux/amd64 ${BASE_IMAGE} AS release-dependencies

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 \
    PYTHONPATH=/opt/bridge:/opt/release-tools \
    PATH=/opt/online3b/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The minimal Ubuntu base lacks CA trust. The helper's certificate bundle
# is pinned and byte-checked for candidate-only TLS transport. Ubuntu signed
# InRelease and Packages SHA256 remain native authenticity authorities.
COPY --from=candidate-transport-ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN test "$(sha256sum /etc/ssl/certs/ca-certificates.crt | cut -d ' ' -f 1)" = \
      '714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5'
COPY deploy/release/bootstrap.sources /etc/apt/sources.list.d/bootstrap.sources
COPY deploy/release/probe-native-bootstrap.sh /usr/local/bin/probe-native-bootstrap
RUN /bin/sh /usr/local/bin/probe-native-bootstrap && \
    test -s /etc/ssl/certs/ca-certificates.crt && \
    mkdir -p /opt/allfather-release/native-evidence && \
    cp -a /native-evidence/. /opt/allfather-release/native-evidence/ && \
    rm -f /etc/apt/sources.list.d/bootstrap.sources && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

# Source-controlled signed Snapshot stanzas are transformed into equivalent
# direct dated snapshot URLs. No moving APT discovery index can select a deb.
COPY deploy/release/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources
COPY deploy/release/prepare-direct-snapshot-sources.sh /usr/local/bin/prepare-direct-snapshot-sources
COPY deploy/release/prepare-native-acquisition.sh /usr/local/bin/prepare-native-acquisition
COPY deploy/release/install-pinned-apt.sh /usr/local/bin/install-pinned-apt
COPY qualification/release-os-apt-selections-v1.txt /usr/local/share/release-os-apt-selections-v1.txt
RUN /bin/sh /usr/local/bin/prepare-direct-snapshot-sources && \
    /bin/sh /usr/local/bin/prepare-native-acquisition && \
    /bin/sh /usr/local/bin/install-pinned-apt && \
    dpkg-query -W -f='${Package}=${Version}\n' \
      | LC_ALL=C sort > /opt/allfather-release/os-packages.txt && \
    test "$(sha256sum /opt/allfather-release/os-packages.txt | cut -d ' ' -f 1)" = \
      '859bca91d5aa9d152424b9d839641fa942a06e55e4af8b5c50c8d7e6258c4f5a' && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

# Identical twelve wheel bytes from the pinned host-built, verified wheelhouse.
COPY build/online3b-wheels/ /opt/allfather-release/wheels/
RUN python3 -m venv /opt/online3b && \
    /opt/online3b/bin/python -m pip install \
      --no-index --find-links=/opt/allfather-release/wheels \
      --require-hashes -r /opt/allfather-release/wheels/runtime-wheels.lock && \
    /opt/online3b/bin/python -m pip freeze --all \
      > /opt/allfather-release/python-packages.txt && \
    /opt/online3b/bin/python -c 'import importlib.metadata as m; assert m.version("pip") == "24.0"'

FROM release-dependencies AS release-application
# No second native or Python dependency installer in the final image.
COPY build/release1-stage/allfather/ /opt/allfather/
COPY build/release1-stage/bridge/ /opt/bridge/
COPY build/release1-stage/tools/ /opt/release-tools/
COPY build/release1-stage/manifest.json /opt/allfather-release/manifest.json
RUN useradd --system --uid 10001 --home-dir /nonexistent \
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
