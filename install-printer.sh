#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BACKEND_DST="/usr/lib/cups/backend/ricohlanfax"
SHARE="/usr/local/share/ricoh-lanfax"
PRINTER_NAME="Ricoh-LAN-Fax"
SPOOL="/var/tmp/ricoh-lanfax"
DESKTOP_DST="/usr/share/applications/Ricoh-LAN-Fax.desktop"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo:  sudo $0"
  exit 1
fi

mkdir -p "$SPOOL/spool" "$SHARE"
chmod 1777 "$SPOOL" "$SPOOL/spool"

rm -rf "$SHARE/ricoh_lanfax"
cp -a "$ROOT/ricoh_lanfax" "$SHARE/ricoh_lanfax"
install -m 755 "$ROOT/start.sh" "$SHARE/start.sh"
find "$SHARE/ricoh_lanfax" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

install -m 700 -o root -g root "$ROOT/cups/ricohlanfax" "$BACKEND_DST"

cancel -a "$PRINTER_NAME" 2>/dev/null || true

if lpstat -p "$PRINTER_NAME" >/dev/null 2>&1; then
  lpadmin -x "$PRINTER_NAME" || true
fi

mkdir -p /usr/share/ppd/ricoh
cp "$ROOT/cups/ricoh-lanfax.ppd" /usr/share/ppd/ricoh/ricoh-lanfax.ppd

lpadmin -p "$PRINTER_NAME" -E -v ricohlanfax:/ \
  -P /usr/share/ppd/ricoh/ricoh-lanfax.ppd \
  -D "Ricoh LAN-Fax (number popup)" -L "IM 350F via Linux"

cupsenable "$PRINTER_NAME" 2>/dev/null || true
cupsaccept "$PRINTER_NAME" 2>/dev/null || true

if [[ -d /usr/share/applications ]]; then
  install -m 644 "$ROOT/Ricoh-LAN-Fax.desktop" "$DESKTOP_DST"
fi

echo
echo "Printer:  $PRINTER_NAME"
echo "Backend:  $BACKEND_DST"
echo "Code:     $SHARE/ricoh_lanfax"
echo
echo "No foreground app is required."
echo "Print to '$PRINTER_NAME' — a number dialog opens."
echo "Set the Ricoh IP in the popup (gear icon)."
echo "Log: $SPOOL/backend.log"
