#!/bin/sh
# Mandatory pre-install byte gate for RELEASE-2B-2 native packages.
# Production must invoke --install in a Docker RUN --network=none layer.
set -eu
phase="${1:-}"
mode="${2:-}"
case "$phase" in bootstrap|runtime) ;; *) echo "invalid native acquisition phase" >&2; exit 40 ;; esac
case "$mode" in --install|--verify-only) ;; *) echo "invalid native gate mode" >&2; exit 41 ;; esac
manifest_dir="${NATIVE_MANIFEST_DIR:-/usr/local/share/release-native-lock}"
cache_dir="${NATIVE_ARCHIVE_DIR:-/var/cache/apt/archives}"
evidence="${NATIVE_EVIDENCE_DIR:-/opt/allfather-release/native-evidence}"
manifest="$manifest_dir/$phase.tsv"
[ -f "$manifest" ] && [ ! -L "$manifest" ] || { echo "native SHA selection manifest missing" >&2; exit 42; }
[ -d "$cache_dir" ] && [ ! -L "$cache_dir" ] || { echo "missing native archive cache" >&2; exit 43; }
mkdir -p "$evidence"
[ ! -L "$evidence" ] || exit 44
set --
seen=0
while IFS="$(printf '\t')" read -r name version architecture expected filename; do
  [ -n "$name" ] && [ -n "$filename" ] || { echo "bad native manifest row" >&2; exit 45; }
  case "$name" in *[!a-z0-9+.-]*|'') echo "unsafe native package" >&2; exit 46 ;; esac
  case "$architecture" in all|amd64) ;; *) echo "wrong native architecture" >&2; exit 47 ;; esac
  case "$filename" in "$name"_*_"$architecture".deb) ;; *) echo "mismatched native archive filename" >&2; exit 48 ;; esac
  case "$expected" in *[!0-9a-f]*|'') echo "bad committed SHA" >&2; exit 49 ;; esac
  [ "${#expected}" -eq 64 ] || { echo "wrong SHA length" >&2; exit 50; }
  path="$cache_dir/$filename"
  [ -f "$path" ] && [ ! -L "$path" ] || { echo "missing/linked native archive: $filename" >&2; exit 51; }
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || { echo "pre-install native SHA differs from committed candidate: $filename" >&2; exit 52; }
  package="$(dpkg-deb -f "$path" Package)"
  installed_version="$(dpkg-deb -f "$path" Version)"
  installed_arch="$(dpkg-deb -f "$path" Architecture)"
  [ "$package" = "$name" ] && [ "$installed_version" = "$version" ] &&
    [ "$installed_arch" = "$architecture" ] || { echo "native package metadata differs from lock" >&2; exit 53; }
  set -- "$@" "$path"
  seen=$((seen + 1))
done < "$manifest"
case "$phase" in bootstrap) wanted=3 ;; runtime) wanted=26 ;; esac
[ "$seen" -eq "$wanted" ] && [ "$#" -eq "$wanted" ] || { echo "native admission manifest incomplete" >&2; exit 54; }
count=0
for p in "$cache_dir"/*; do
  [ -e "$p" ] || [ -L "$p" ] || continue
  case "$p" in
    "$cache_dir"/lock|"$cache_dir"/partial) continue ;;
    "$cache_dir"/*.deb)
      [ -f "$p" ] && [ ! -L "$p" ] || { echo "linked/non-file native cache entry" >&2; exit 55; }
      b="${p##*/}"
      grep -Fq "$(printf '\t')$b" "$manifest" ||
        { echo "unapproved extra native archive: $b" >&2; exit 56; }
      count=$((count + 1)) ;;
    *) echo "unapproved native cache entry: $p" >&2; exit 57 ;;
  esac
done
[ "$count" -eq "$wanted" ] || { echo "extra/missing native archive cache" >&2; exit 58; }
cp "$manifest" "$evidence/$phase-preinstall-verified.tsv"
[ "$mode" = "--install" ] || { echo "Native $phase pre-install SHA gate verified"; exit 0; }
# Only verified local operands, --no-download, and a network-isolated Docker
# RUN may execute installation. No apt-get update or URI-based candidates.
apt-get install -y --no-download --no-install-recommends "$@"
echo "Native $phase exact offline installation completed"
