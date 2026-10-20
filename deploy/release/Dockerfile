# RELEASE-2B-2: offline candidate, never public release authorization.
# Signed byte acquisition is a private, source-bound cache transport. The
# digest-pinned Python stage rechecks full GPG->Packages->.deb provenance.
# Ubuntu itself rechecks GPG and installs only admitted .deb bytes offline.
ARG BASE_IMAGE
FROM --platform=linux/amd64 ${BASE_IMAGE} AS pinned-ubuntu-keyring
FROM --platform=linux/amd64 docker.io/library/python@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS candidate-transport-ca
COPY --from=pinned-ubuntu-keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg /native-keyring.gpg
COPY qualification/release-os-packages.lock /native-source/qualification/release-os-packages.lock
COPY qualification/release-bootstrap-v1.json /native-source/qualification/release-bootstrap-v1.json
COPY qualification/release-image-v1.json /native-source/qualification/release-image-v1.json
COPY qualification/release-os-apt-selections-v1.txt /native-source/qualification/release-os-apt-selections-v1.txt
COPY deploy/release/ubuntu.sources /native-source/deploy/release/ubuntu.sources
COPY scripts/verify-release-native-index-chain.py /native-source/scripts/verify-release-native-index-chain.py
COPY scripts/prepare-release-native-bundle.py /native-source/scripts/prepare-release-native-bundle.py
COPY scripts/compile-release-native-selection.py /native-source/scripts/compile-release-native-selection.py
COPY build/release2b2-native-bundle/ /signed-native-bundle/
RUN python3 /native-source/scripts/prepare-release-native-bundle.py \
      --root /native-source --bundle /signed-native-bundle --keyring /native-keyring.gpg && \
    python3 /native-source/scripts/compile-release-native-selection.py \
      --lock /native-source/qualification/release-os-packages.lock --out /native-selection

FROM --platform=linux/amd64 ${BASE_IMAGE} AS release-dependencies
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 \
    PYTHONPATH=/opt/bridge:/opt/release-tools \
    PATH=/opt/online3b/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

COPY --from=candidate-transport-ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN test "$(sha256sum /etc/ssl/certs/ca-certificates.crt | cut -d ' ' -f 1)" = \
      '714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5'
COPY deploy/release/bootstrap.sources /etc/apt/sources.list.d/bootstrap.sources
COPY --from=candidate-transport-ca /native-selection/ /usr/local/share/release-native-lock/
COPY deploy/release/preinstall-native-gate.sh /usr/local/bin/preinstall-native-gate
COPY deploy/release/finalize-native-bootstrap.sh /usr/local/bin/finalize-native-bootstrap
COPY deploy/release/verify-native-bundle-signatures.sh /usr/local/bin/verify-native-bundle-signatures
COPY --from=candidate-transport-ca /signed-native-bundle/dists/ /opt/allfather-release/native-input/dists/
COPY --from=candidate-transport-ca /signed-native-bundle/evidence/bootstrap/ /native-evidence/
COPY --from=candidate-transport-ca /signed-native-bundle/archives/bootstrap/ /var/cache/apt/archives/
RUN /bin/sh /usr/local/bin/verify-native-bundle-signatures && \
    test "$(sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | cut -d ' ' -f 1)" = \
      '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
RUN --network=none rm -f /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources && \
    LC_ALL=C dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort \
      > /native-evidence/base-packages.txt && \
    test "$(sha256sum /native-evidence/base-packages.txt | cut -d ' ' -f 1)" = \
      'c9ec22da6aac2bed9dff11162a66e5bb4bf4c15f3485528ac4358901cb1156f5' && \
    sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | cut -d ' ' -f 1 \
      > /native-evidence/keyring.sha256 && \
    sha256sum /etc/ssl/certs/ca-certificates.crt | cut -d ' ' -f 1 \
      > /native-evidence/transport-ca.sha256 && \
    /bin/sh /usr/local/bin/preinstall-native-gate bootstrap --install && \
    /bin/sh /usr/local/bin/finalize-native-bootstrap && \
    mkdir -p /opt/allfather-release/native-evidence && \
    cp -a /native-evidence/. /opt/allfather-release/native-evidence/ && \
    rm -f /etc/apt/sources.list.d/bootstrap.sources && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

COPY deploy/release/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources
COPY deploy/release/prepare-direct-snapshot-sources.sh /usr/local/bin/prepare-direct-snapshot-sources
COPY --from=candidate-transport-ca /signed-native-bundle/evidence/runtime/ /opt/allfather-release/native-evidence/
COPY --from=candidate-transport-ca /signed-native-bundle/archives/runtime/ /var/cache/apt/archives/
COPY qualification/release-os-apt-selections-v1.txt /usr/local/share/release-os-apt-selections-v1.txt
RUN /bin/sh /usr/local/bin/prepare-direct-snapshot-sources
RUN --network=none /bin/sh /usr/local/bin/preinstall-native-gate runtime --install && \
    dpkg-query -W -f='${Package}=${Version}\n' \
      | LC_ALL=C sort > /opt/allfather-release/os-packages.txt && \
    test "$(sha256sum /opt/allfather-release/os-packages.txt | cut -d ' ' -f 1)" = \
      '859bca91d5aa9d152424b9d839641fa942a06e55e4af8b5c50c8d7e6258c4f5a' && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

COPY build/online3b-wheels/ /opt/allfather-release/wheels/
RUN python3 -m venv /opt/online3b && \
    /opt/online3b/bin/python -m pip install \
      --no-index --find-links=/opt/allfather-release/wheels \
      --require-hashes -r /opt/allfather-release/wheels/runtime-wheels.lock && \
    /opt/online3b/bin/python -m pip freeze --all \
      > /opt/allfather-release/python-packages.txt && \
    /opt/online3b/bin/python -c 'import importlib.metadata as m; assert m.version("pip") == "24.0"'

FROM release-dependencies AS release-application
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
