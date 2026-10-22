# ONLINE-PLAY-1 PR-A: local candidate ONLY. Never publish, push or tag for release.
# BASE_IMAGE MUST be a digest-verified, locally built RELEASE-1 offline image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/work:/opt/release-tools:/opt/bridge \
    ALLFATHER_CANARY_SCOPE=private-candidate-only
# Place live code OUTSIDE /opt/release-tools; its offline source-file manifest
# and installed verify-release-candidate contract remain immutable.
COPY deploy/live_canary/ /opt/live-canary/deploy/live_canary/
COPY deploy/lichess/live_*.py /opt/live-canary/deploy/lichess/
COPY qualification/online-live-canary-v1.json /opt/live-canary/policy.json
RUN /opt/online3b/bin/python /opt/release-tools/scripts/verify-release-candidate.py --installed && \
    /opt/online3b/bin/python -m compileall -q /opt/live-canary
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/opt/online3b/bin/python","/opt/live-canary/deploy/live_canary/entrypoint.py"]
CMD ["verify-only"]
