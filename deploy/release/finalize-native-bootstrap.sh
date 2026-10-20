#!/bin/sh
# Post-install CA verification, separate from signed acquisition and offline
# pre-install package byte admission.
set -eu
root=/native-evidence
test -s /etc/ssl/certs/ca-certificates.crt
dpkg-query -W -f='${Version}\n' ca-certificates | grep -Fx '20260601~24.04.1'
dpkg-query -W -f='${Version}\n' openssl | grep -Fx '3.0.13-0ubuntu3.16'
dpkg-query -W -f='${Version}\n' libssl3t64 | grep -Fx '3.0.13-0ubuntu3.16'
update-ca-certificates --fresh >/dev/null
LC_ALL=C dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort > "$root/bootstrap-packages.txt"
printf 'SIGNED_APT_SNAPSHOT_PROBE_COMPLETE\n' > "$root/disposition.txt"
