#!/bin/sh
# APT acquisition from the immutable, GPG-signed Ubuntu snapshot configured
# in ubuntu.sources. Only transient 429/502/503/504 responses may be retried.
# All signature, hash and certificate errors fail immediately.
set -eu
APT_LISTS_DIR="${APT_LISTS_DIR:-/var/lib/apt/lists}"
APT_RETRY_BACKOFF_SECONDS="${APT_RETRY_BACKOFF_SECONDS:-4}"
APT_LOG="${APT_LOG:-/tmp/allfather-release-apt-$$.log}"
trap 'rm -f "$APT_LOG"' 0 HUP INT TERM

only_transient_acquisition_failures() {
    # APT writes one Err:<index> plus its indented reason, and often repeats
    # it as "E: Failed to fetch". Every recorded failure must have an
    # explicitly allowed transient HTTP status. One unrelated DNS/TLS/hash/
    # signature/unclassified error vetoes retry even alongside a valid 503.
    LC_ALL=C awk '
        function transient(s) {
            return s ~ /(^|[[:space:]])(429|502|503|504)([[:space:]]|$)/
        }
        function invalid_status(s, a,n,i,t) {
            n=split(s,a,/[[:space:]]+/)
            for(i=1;i<=n;i++) {
                t=a[i]
                gsub(/^[^0-9]+|[^0-9]+$/, "", t)
                if(t ~ /^[45][0-9][0-9]$/ &&
                   t!="429" && t!="502" && t!="503" && t!="504")
                    return 1
            }
            return 0
        }
        function nontransient(s) {
            return s ~ /NO_PUBKEY|BADSIG|EXPKEYSIG|REVKEYSIG|Hash Sum mismatch|certificate|Certificate|TLS|SSL|signatures could not|not signed|does not have a Release file|clearsigned|unexpected size|Temporary failure resolving|Could not resolve|Name or service not known|Connection refused|Network is unreachable|Unable to locate package|Could not get lock|Permission denied/ ||
                   invalid_status(s)
        }
        function finish_err() {
            if (in_err && !err_retryable) bad=1
            in_err=0
            err_retryable=0
        }
        {
            line=$0
            if (nontransient(line)) bad=1
            if (line ~ /^Err:[0-9]+[[:space:]]/) {
                finish_err()
                in_err=1
                err_retryable=transient(line)
                seen++
                next
            }
            if (in_err && line ~ /^[[:space:]]+/) {
                if(transient(line)) err_retryable=1
                next
            }
            finish_err()
            if (line ~ /^E: Failed to fetch /) {
                seen++
                if (!transient(line)) bad=1
                next
            }
            if (line ~ /^E:/) {
                # This APT summary follows individually classified errors.
                if (line !~ /^E: Some index files failed to download/)
                    bad=1
                next
            }
            if (line ~ /^(error:|Error:|fatal:)/) bad=1
        }
        END {
            finish_err()
            exit !(seen>0 && bad==0)
        }
    ' "$APT_LOG"
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
        if ! only_transient_acquisition_failures; then
            echo "Pinned signed APT $phase unclassified/integrity failure" >&2
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
