#!/usr/bin/env bash
# Bound Ubuntu package installation for the hosted qualification workflows.
# The official signed Ubuntu archive is used in an isolated APT sources/list
# namespace; runner-provided Azure mirror lists are not consulted.
set -euo pipefail

# CI prerequisite acquisition is not the signed immutable RELEASE-2B substrate.
# Skip the mutable index entirely when the hosted image already satisfies the
# explicit required-package set; otherwise keep a bounded signed acquisition.
APT_UPDATE_TIMEOUT_SEC=210
APT_INSTALL_TIMEOUT_SEC=180
APT_NETWORK_TIMEOUT_SEC=25
APT_MAX_RETRIES=2
APT_KEYRING=/usr/share/keyrings/ubuntu-archive-keyring.gpg

render_sources() {
  local codename="${1:?Ubuntu codename required}"
  local arch="${2:?DPKG architecture required}"
  case "$codename" in
    jammy|noble) ;;
    *) echo "unsupported Ubuntu codename: $codename" >&2; return 2 ;;
  esac
  local archive security
  case "$arch" in
    amd64)
      archive=https://archive.ubuntu.com/ubuntu
      security=https://security.ubuntu.com/ubuntu
      ;;
    arm64)
      archive=https://ports.ubuntu.com/ubuntu-ports
      security=https://ports.ubuntu.com/ubuntu-ports
      ;;
    *) echo "unsupported Ubuntu architecture: $arch" >&2; return 2 ;;
  esac
  cat <<EOF
Types: deb
URIs: $archive
Suites: $codename $codename-updates $codename-backports
Components: main restricted universe multiverse
Architectures: $arch
Signed-By: $APT_KEYRING

Types: deb
URIs: $security
Suites: $codename-security
Components: main restricted universe multiverse
Architectures: $arch
Signed-By: $APT_KEYRING
EOF
}

# All package operations share precisely the same signed sources and fresh
# package indexes. No cached Azure mirror lists or trusted=yes overrides.
apt_run() {
  local state_dir="$1" duration="$2"
  shift 2
  timeout --signal=TERM --kill-after=5s "${duration}s" \
    sudo env DEBIAN_FRONTEND=noninteractive apt-get \
      -o "Dir::Etc::sourcelist=/dev/null" \
      -o "Dir::Etc::sourceparts=$state_dir/sources.list.d" \
      -o "Dir::State::lists=$state_dir/lists" \
      -o "APT::Update::Error-Mode=any" \
      -o "Acquire::Languages=none" \
      -o "Acquire::IndexTargets::deb::CNF::DefaultEnabled=false" \
      -o "Acquire::Retries=$APT_MAX_RETRIES" \
      -o "Acquire::http::Timeout=$APT_NETWORK_TIMEOUT_SEC" \
      -o "Acquire::https::Timeout=$APT_NETWORK_TIMEOUT_SEC" \
      -o "DPkg::Lock::Timeout=20" \
      "$@"
}

install_packages() (
  local codename="$1" arch="$2"
  shift 2
  if (($# == 0)); then
    echo "No APT package arguments provided" >&2
    return 2
  fi
  for package in "$@"; do
    if [[ ! "$package" =~ ^[a-z0-9][a-z0-9+.-]*$ ]]; then
      echo "Invalid APT package argument: $package" >&2
      return 2
    fi
  done
  if [[ ! -s "$APT_KEYRING" ]]; then
    echo "Ubuntu archive signing keyring missing: $APT_KEYRING" >&2
    return 2
  fi
  # Installed host tools remain an observed CI input, not a promotion of
  # runtime OS/wheel release locks. Never use an unverified package as proof
  # of an immutable build: downstream exact-source qualification still gates it.
  local all_installed=1 installed_state
  for package in "$@"; do
    installed_state="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
    if [[ "$installed_state" != "install ok installed" ]]; then
      all_installed=0
      break
    fi
  done
  if ((all_installed)); then
    echo "CI host prerequisites already installed; no APT index acquisition"
    dpkg-query -W --showformat='${Package}=${Version}\n' "$@"
    return 0
  fi
  local state_dir
  state_dir="$(mktemp -d)"
  # root-owned APT lists/partial cannot be deleted by an unprivileged trap.
  # Never let cleanup change the original acquisition/install exit code.
  trap 'rc=$?; trap - EXIT; sudo rm -rf -- "$state_dir" || echo "APT temp cleanup failed" >&2; exit "$rc"' EXIT
  mkdir -p "$state_dir/sources.list.d" "$state_dir/lists/partial"
  render_sources "$codename" "$arch" > "$state_dir/sources.list.d/ubuntu.sources"
  echo "APT qualification bootstrap: Ubuntu=$codename arch=$arch"
  echo "APT source: signed official Ubuntu HTTPS archive (isolated lists)"
  echo "APT update timeout: ${APT_UPDATE_TIMEOUT_SEC}s; install timeout: ${APT_INSTALL_TIMEOUT_SEC}s"
  if apt_run "$state_dir" "$APT_UPDATE_TIMEOUT_SEC" update; then
    echo "APT official package indexes refreshed successfully"
  else
    status=$?
    echo "APT update failed/expired (exit $status); no stale-index fallback" >&2
    return "$status"
  fi
  if apt_run "$state_dir" "$APT_INSTALL_TIMEOUT_SEC" install -y "$@"; then
    echo "APT dependencies installed"
  else
    status=$?
    echo "APT installation failed/expired (exit $status)" >&2
    return "$status"
  fi
  echo "APT installed package versions:"
  dpkg-query -W --showformat='${Package}=${Version}\n' "$@"
)

main() {
  if [[ "${1:-}" == "--render-sources" ]]; then
    if (($# != 3)); then
      echo "Usage: $0 --render-sources {jammy|noble} {amd64|arm64}" >&2
      return 2
    fi
    render_sources "$2" "$3"
    return
  fi
  if (($# == 0)); then
    echo "Usage: $0 <APT package> [<APT package> ...]" >&2
    return 2
  fi
  # GitHub-hosted runners must be supported Ubuntu; never guess a release.
  # shellcheck source=/etc/os-release
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "APT qualification bootstrap only supports Ubuntu runners" >&2
    return 2
  fi
  install_packages "${VERSION_CODENAME:-}" "$(dpkg --print-architecture)" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
