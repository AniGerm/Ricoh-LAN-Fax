#!/usr/bin/env bash
# Production install: CUPS printer + fax popup (phonebook/cover/preview).
# Does not install the lab sink desktop app (use ./start.sh from a git checkout).
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BACKEND_DST="/usr/lib/cups/backend/ricohlanfax"
SHARE="/usr/local/share/ricoh-lanfax"
PRINTER_NAME="Ricoh-LAN-Fax"
SPOOL="/var/tmp/ricoh-lanfax"
DESKTOP_DST="/usr/share/applications/Ricoh-LAN-Fax.desktop"
BIN_DST="/usr/local/bin/ricoh-lanfax"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo:  sudo $0"
  exit 1
fi

mkdir -p "$SPOOL/spool" "$SHARE"
chmod 1777 "$SPOOL" "$SPOOL/spool"

rm -rf "$SHARE/ricoh_lanfax"
cp -a "$ROOT/ricoh_lanfax" "$SHARE/ricoh_lanfax"
find "$SHARE/ricoh_lanfax" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Lab-only launchers stay out of the installed product.
rm -f "$SHARE/start.sh" "$DESKTOP_DST"

install -m 755 "$ROOT/ricoh-lanfax" "$BIN_DST"
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

echo
echo "Installed production printer (no sink/lab app)."
echo "Printer:  $PRINTER_NAME"
echo "Backend:  $BACKEND_DST"
echo "Code:     $SHARE/ricoh_lanfax"
echo "CLI:      $BIN_DST"
echo
echo "Print to '$PRINTER_NAME' — a number dialog opens (phonebook, cover, preview)."
echo "Set the Ricoh IP in the popup (gear icon)."
echo "Log: $SPOOL/backend.log"
echo
echo "Lab sink (developers only, from git checkout): ./start.sh"
