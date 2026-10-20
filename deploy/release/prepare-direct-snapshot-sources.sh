#!/bin/sh
# RELEASE-2B-2: turn reviewed Snapshot deb822 stanzas into direct immutable URLs.
# This avoids APT 2.8's moving discovery indexes entirely. Source identity
# remains governed by source-controlled ubuntu.sources + qualification policy.
set -eu
original=/etc/apt/sources.list.d/ubuntu.sources
witness=/opt/allfather-release/native-evidence
test -f "$original" && test -s "$original"
test "$(grep -c '^Snapshot: 20261001T000000Z$' "$original")" -eq 2
test "$(grep -c '^Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg$' "$original")" -eq 2
test "$(grep -c '^URIs: https://archive.ubuntu.com/ubuntu$' "$original")" -eq 1
test "$(grep -c '^URIs: https://security.ubuntu.com/ubuntu$' "$original")" -eq 1
test "$(grep -c '^Architectures: amd64$' "$original")" -eq 2
! grep -Eiq 'trusted=yes|allow-unauthenticated|Verify-Peer=false|Verify-Host=false' "$original"
mkdir -p "$witness"
cp "$original" "$witness/source-ubuntu-declared.sources"
awk '
  /^URIs: https:\/\/archive.ubuntu.com\/ubuntu$/ ||
  /^URIs: https:\/\/security.ubuntu.com\/ubuntu$/ {
    print "URIs: https://snapshot.ubuntu.com/ubuntu/20261001T000000Z"
    next
  }
  /^Snapshot: 20261001T000000Z$/ { next }
  { print }
' "$original" > "$original.next"
test "$(grep -c '^URIs: https://snapshot.ubuntu.com/ubuntu/20261001T000000Z$' "$original.next")" -eq 2
! grep -Eq '^Snapshot:|^URIs: (http:|https://(archive|security)\.ubuntu\.com)' "$original.next"
mv "$original.next" "$original"
cp "$original" "$witness/source-ubuntu-effective.sources"
# Current/discovery indexes may never be mixed into a permitted download.
rm -rf /var/lib/apt/lists/*
mkdir -p /var/lib/apt/lists/partial
