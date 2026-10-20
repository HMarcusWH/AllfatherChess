#!/bin/sh
# RELEASE-2B-2: disposable signed HTTP snapshot acquisition probe.
# No fallback, no live/public release, no dependency lock approval.
set -eu
export DEBIAN_FRONTEND=noninteractive
mkdir -p /native-evidence
test -f /etc/apt/sources.list.d/bootstrap.sources
test "$(grep -c '^Snapshot: 20261001T000000Z$' /etc/apt/sources.list.d/bootstrap.sources)" -eq 2
test "$(grep -c '^Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg$' /etc/apt/sources.list.d/bootstrap.sources)" -eq 2
test -s /usr/share/keyrings/ubuntu-archive-keyring.gpg
sha256sum /usr/share/keyrings/ubuntu-archive-keyring.gpg | awk '{print $1}' > /native-evidence/keyring.sha256
# Transport CA is only a pinned helper-image diagnostic input, not a native
# package or approved release root. Record its exact bytes separately.
sha256sum /etc/ssl/certs/ca-certificates.crt | awk '{print $1}' > /native-evidence/transport-ca.sha256
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources
for f in /etc/apt/sources.list.d/*; do
  [ ! -e "$f" ] && continue
  [ "$f" = "/etc/apt/sources.list.d/bootstrap.sources" ] ||
    { echo "unapproved APT source $f" >&2; exit 29; }
done
LC_ALL=C dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort > /native-evidence/base-packages.txt
rm -rf /var/lib/apt/lists/*
mkdir -p /var/lib/apt/lists/partial
# APT signature and index-hash validation must succeed.
apt-get update -o APT::Update::Error-Mode=any -o Acquire::Retries=0
# APT 2.8 first fetches live signed Ubuntu release metadata to discover the
# snapshot facility, *then* fetches the specified immutable snapshot. The
# former is not permitted to supply a package candidate or installation.
# Remove every non-snapshot package index before any apt-cache lookup/install.
discovery=0
for item in /var/lib/apt/lists/*; do
  [ -f "$item" ] || continue
  case "$item" in
    */lock) ;;
    *snapshot.ubuntu.com_ubuntu_20261001T000000Z*) ;;
    *archive.ubuntu.com_ubuntu*|*security.ubuntu.com_ubuntu*)
      rm -f "$item"
      discovery=$((discovery + 1))
      ;;
    *) echo "unexpected non-snapshot APT index: $item" >&2; exit 30 ;;
  esac
done
[ "$discovery" -gt 0 ] || { echo "missing observed snapshot-discovery metadata" >&2; exit 34; }
echo "Removed $discovery discovery-only current Ubuntu indexes; package selection snapshot-only"
count=0
: > /native-evidence/inrelease-sha256.txt
for item in /var/lib/apt/lists/*_InRelease; do
  [ -f "$item" ] || continue
  case "$item" in
    *20261001T000000Z*_InRelease) ;;
    *) echo "non-snapshot APT InRelease: $item" >&2; exit 31 ;;
  esac
  # Prove every retained snapshot Release is actually signed by the pinned
  # Ubuntu archive keyring, independently of APT's successful update.
  gpgv --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg "$item" >&2
  sha256sum "$item" | sed 's|/var/lib/apt/lists/||' >> /native-evidence/inrelease-sha256.txt
  count=$((count + 1))
done
[ "$count" -ge 3 ] || { echo "missing signed snapshot InRelease" >&2; exit 32; }
LC_ALL=C sort -o /native-evidence/inrelease-sha256.txt /native-evidence/inrelease-sha256.txt
cd /tmp
version='20260601~24.04.1'
apt-cache show "ca-certificates=$version" > /native-evidence/ca-package-index.txt
test -s /native-evidence/ca-package-index.txt
apt-get download "ca-certificates=$version"
set -- ca-certificates_*_all.deb
test "$#" -eq 1 && test -f "$1"
actual="$(sha256sum "$1" | awk '{print $1}')"
grep -Fqx "SHA256: $actual" /native-evidence/ca-package-index.txt ||
  { echo "CA bytes disagree with authenticated package-index SHA" >&2; exit 33; }
printf '%s\n' "$actual" > /native-evidence/ca-deb.sha256
dpkg-deb -f "$1" Package | grep -Fx 'ca-certificates'
dpkg-deb -f "$1" Version | grep -Fx "$version"
apt-get install -y --no-install-recommends "ca-certificates=$version"
test -s /etc/ssl/certs/ca-certificates.crt
dpkg-query -W -f='${Version}\n' ca-certificates | grep -Fx "$version"
LC_ALL=C dpkg-query -W -f='${Package}=${Version}\n' | LC_ALL=C sort > /native-evidence/bootstrap-packages.txt
printf 'SIGNED_APT_SNAPSHOT_PROBE_COMPLETE\n' > /native-evidence/disposition.txt
