#!/bin/sh
# Record package source evidence before apt-get may discard downloaded .debs.
# Strictly source-frozen, selected direct versions supplied by reviewed list.
set -eu
list=/opt/allfather-release/native-evidence
inputs=/usr/local/share/release-os-apt-selections-v1.txt
test -f "$inputs" && test -s "$inputs"
test "$(wc -l < "$inputs" | tr -d ' ')" -eq 10
test "$(grep -c '^URIs: https://snapshot.ubuntu.com/ubuntu/20261001T000000Z$' /etc/apt/sources.list.d/ubuntu.sources)" -eq 2
apt-get update -o APT::Update::Error-Mode=any -o Acquire::Retries=0
# Direct snapshots, no moving discovery; reject *every* unrecognized index.
mkdir -p "$list"
: > "$list/runtime-inrelease-sha256.txt"
count=0
for f in /var/lib/apt/lists/*_InRelease; do
  test -f "$f" || continue
  case "$f" in
    *snapshot.ubuntu.com_ubuntu_20261001T000000Z*_InRelease) ;;
    *) echo "unexpected APT origin $f" >&2; exit 23 ;;
  esac
  gpgv --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg "$f" >&2
  sha256sum "$f" | sed 's|/var/lib/apt/lists/||' >> "$list/runtime-inrelease-sha256.txt"
  count=$((count+1))
done
test "$count" -eq 4
LC_ALL=C sort -o "$list/runtime-inrelease-sha256.txt" "$list/runtime-inrelease-sha256.txt"
set --
while IFS= read -r selection; do
  case "$selection" in
    *=*) ;;
    *) echo "bad exact native selection" >&2; exit 24 ;;
  esac
  test -n "$selection"
  set -- "$@" "$selection"
done < "$inputs"
test "$#" -eq 10
# Download-only has no installation authority; APT still verifies hashes
# against Packages authenticated by those signed InRelease metadata files.
apt-get install -y --download-only --no-install-recommends "$@"
mkdir -p "$list/runtime-index-records"
: > "$list/runtime-debs.tsv"
count=0
for deb in /var/cache/apt/archives/*.deb; do
  test -f "$deb" || continue
  name="$(dpkg-deb -f "$deb" Package)"
  version="$(dpkg-deb -f "$deb" Version)"
  arch="$(dpkg-deb -f "$deb" Architecture)"
  sha="$(sha256sum "$deb" | awk '{print $1}')"
  case "$name" in
    *[!a-z0-9+.-]*|"") echo "unsafe Debian package name" >&2; exit 25 ;;
  esac
  case "$arch" in amd64|all) ;; *) echo "non-native architecture" >&2; exit 26 ;; esac
  record="$list/runtime-index-records/$name.txt"
  apt-cache show "$name=$version" > "$record"
  grep -Fqx "SHA256: $sha" "$record" || { echo "APT index hash mismatch: $name" >&2; exit 27; }
  grep -Fqx "Package: $name" "$record"
  grep -Fqx "Version: $version" "$record"
  grep -Fqx "Architecture: $arch" "$record"
  printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$version" "$arch" "$sha" "$(basename "$deb")" >> "$list/runtime-debs.tsv"
  count=$((count+1))
done
test "$count" -ge 1 && test "$count" -le 80
LC_ALL=C sort -o "$list/runtime-debs.tsv" "$list/runtime-debs.tsv"
cp "$inputs" "$list/runtime-direct-selections.txt"
