#!/bin/sh
# APT acquisition from the immutable, GPG-signed Ubuntu snapshot configured
# in ubuntu.sources. Only transient 429/502/503/504 responses may be retried.
# All signature, hash and certificate errors fail immediately.
set -eu
APT_LISTS_DIR="${APT_LISTS_DIR:-/var/lib/apt/lists}"
APT_RETRY_BACKOFF_SECONDS="${APT_RETRY_BACKOFF_SECONDS:-4}"
APT_LOG="${APT_LOG:-/tmp/allfather-release-apt-$$.log}"
trap 'rm -f "$APT_LOG"' 0 HUP INT TERM

retryable() {
    grep -Eq '(^|[[:space:]])(429|502|503|504)([[:space:]]|$)|Service Unavailable|Too Many Requests|Bad Gateway|Gateway Timeout|Gateway Time-out' "$APT_LOG"
}

integrity_error() {
    grep -Eiq 'NO_PUBKEY|BADSIG|EXPKEYSIG|REVKEYSIG|Hash Sum mismatch|certificate (verification|verify)|signatures could not|not signed|does not have a Release file|clearsigned file.*invalid|File has unexpected size|401 Unauthorized|403 Forbidden|404 Not Found' "$APT_LOG"
}

apt_attempt() {
    phase="$1"
    shift
    attempt=1
    while [ "$attempt" -le 4 ]; do
        if [ "$phase" = "update" ]; then
            # Every failure destroys partial/stale lists before retrying.
            rm -rf "$APT_LISTS_DIR"/*
            mkdir -p "$APT_LISTS_DIR/partial"
        fi
        if apt-get \
            -o "Dir::State::lists=$APT_LISTS_DIR" \
            -o Acquire::Retries=1 \
            -o Acquire::https::Timeout=25 \
            "$phase" "$@" > "$APT_LOG" 2>&1; then
            echo "Pinned signed APT $phase completed on attempt $attempt"
            return 0
        fi
        cat "$APT_LOG" >&2
        if integrity_error || ! retryable; then
            echo "Pinned signed APT $phase integrity/nontransient failure" >&2
            return 100
        fi
        if [ "$attempt" -eq 4 ]; then
            echo "Pinned signed APT $phase exhausted four bounded attempts" >&2
            return 100
        fi
        sleep $((attempt * APT_RETRY_BACKOFF_SECONDS))
        attempt=$((attempt + 1))
    done
    return 100
}

apt_attempt update -o APT::Update::Error-Mode=any
apt_attempt install -y --no-install-recommends \
    python3 python3-venv python3-pip ca-certificates procps \
    libopenblas0-pthread libprotobuf32t64 libstdc++6 libgomp1 zlib1g
rm -rf "$APT_LISTS_DIR"/*
