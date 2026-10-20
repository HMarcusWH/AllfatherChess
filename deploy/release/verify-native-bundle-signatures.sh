#!/bin/sh
# Verify the exact source-cached Ubuntu InRelease GPG signatures inside the
# pinned Ubuntu base, independently of the Python bundle verifier. Cache
# transport availability is NEVER proof of signed package identity.
set -eu
raw=/opt/allfather-release/native-input/dists
key=/usr/share/keyrings/ubuntu-archive-keyring.gpg
test -s "$key" && test ! -L "$key"
for suite in noble noble-updates noble-backports noble-security; do
  item="$raw/$suite/InRelease"
  test -s "$item" && test ! -L "$item" ||
    { echo "missing cached frozen Ubuntu signed suite $suite" >&2; exit 71; }
  gpgv --keyring "$key" "$item" >&2 ||
    { echo "invalid cached Ubuntu signature $suite" >&2; exit 72; }
done
echo "Pinned Ubuntu four-suite cached InRelease signatures PASS"
