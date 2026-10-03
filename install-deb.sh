#!/usr/bin/env bash
# Install a ricoh-lanfax_*.deb safely:
# 1) install missing dependencies first (python3-tk, …)
# 2) copy the .deb to /tmp so apt/_apt can read it (avoids Downloads sandbox warning)
# 3) install from /tmp
set -euo pipefail

DEPS=(python3 python3-tk python3-pil ghostscript cups cups-client cups-bsd)

usage() {
  echo "Usage: sudo $0 [path/to/ricoh-lanfax_VERSION_all.deb]"
  echo "If no path is given, uses the newest ricoh-lanfax_*.deb next to this script or in ~/Downloads."
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo:  sudo $0 ${1:-}"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REAL_USER="${SUDO_USER:-${USER:-}}"
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
REAL_HOME="${REAL_HOME:-/home/$REAL_USER}"

pick_deb() {
  local given="${1:-}"
  if [[ -n "$given" ]]; then
    if [[ ! -f "$given" ]]; then
      echo "Deb not found: $given" >&2
      exit 1
    fi
    readlink -f "$given"
    return
  fi
  local found=""
  found="$(ls -1t "$SCRIPT_DIR"/ricoh-lanfax_*_all.deb 2>/dev/null | head -n1 || true)"
  if [[ -z "$found" && -d "$REAL_HOME/Downloads" ]]; then
    found="$(ls -1t "$REAL_HOME"/Downloads/ricoh-lanfax_*_all.deb 2>/dev/null | head -n1 || true)"
  fi
  if [[ -z "$found" ]]; then
    echo "No ricoh-lanfax_*.deb found. Pass the path explicitly." >&2
    usage >&2
    exit 1
  fi
  readlink -f "$found"
}

DEB_SRC="$(pick_deb "${1:-}")"
DEB_TMP="/tmp/ricoh-lanfax-install.deb"

echo "Source:  $DEB_SRC"
echo "Staging: $DEB_TMP"
echo

echo "==> Installing dependencies (if missing)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y "${DEPS[@]}"

echo
echo "==> Copying .deb to /tmp (readable by apt)…"
cp -f "$DEB_SRC" "$DEB_TMP"
chmod 644 "$DEB_TMP"

echo "==> Installing package from /tmp…"
apt-get install -y "$DEB_TMP"

echo
echo "Done."
echo "Printer queue: Ricoh-LAN-Fax"
echo "Settings app:  Ricoh LAN-Fax  (Ubuntu menu)  or:  ricoh-lanfax settings"
echo "Quick check:   ricoh-lanfax --version"
