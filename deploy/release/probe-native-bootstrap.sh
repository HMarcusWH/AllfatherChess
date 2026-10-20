#!/bin/sh
# RELEASE-2B-2: disposable signed HTTP snapshot acquisition probe.
# No fallback, no live/public release, no dependency lock approval.
set -eu
[ "${1:-}" = "--acquire-only" ] || { echo "bootstrap only accepts --acquire-only; installation requires pre-install gate" >&2; exit 39; }
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
test "$(cat /native-evidence/keyring.sha256)" = '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
test "$(cat /native-evidence/transport-ca.sha256)" = '714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5'
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
test "$actual" = '6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50'
# Retain exact transitive native bootstrap input hashes before installation.
# The 92-row base and 94-row post-bootstrap inventories are distinct.
apt-get install -y --download-only --no-install-recommends "ca-certificates=$version"
mkdir -p /native-evidence/bootstrap-index-records
: > /native-evidence/bootstrap-debs.tsv
for deb in /var/cache/apt/archives/*.deb; do
  test -f "$deb" || continue
  pkg="$(dpkg-deb -f "$deb" Package)"
  ver="$(dpkg-deb -f "$deb" Version)"
  arch="$(dpkg-deb -f "$deb" Architecture)"
  case "$pkg" in
    ca-certificates|openssl|libssl3t64) ;;
    *) echo "unexpected bootstrap acquired package $pkg" >&2; exit 35 ;;
  esac
  case "$arch" in amd64|all) ;; *) echo "unexpected bootstrap architecture" >&2; exit 36 ;; esac
  package_sha="$(sha256sum "$deb" | awk '{print $1}')"
  apt-cache show "$pkg=$ver" > "/native-evidence/bootstrap-index-records/$pkg.txt"
  grep -Fqx "SHA256: $package_sha" "/native-evidence/bootstrap-index-records/$pkg.txt"
  printf '%s\t%s\t%s\t%s\t%s\n' "$pkg" "$ver" "$arch" "$package_sha" "$(basename "$deb")" >> /native-evidence/bootstrap-debs.tsv
done
LC_ALL=C sort -o /native-evidence/bootstrap-debs.tsv /native-evidence/bootstrap-debs.tsv
dpkg-deb -f "$1" Package | grep -Fx 'ca-certificates'
dpkg-deb -f "$1" Version | grep -Fx "$version"
rm -f "$1"
test "$(wc -l < /native-evidence/bootstrap-debs.tsv | tr -d ' ')" -eq 3
echo "Signed Ubuntu bootstrap acquired; no packages installed in this step"
