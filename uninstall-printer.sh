#!/usr/bin/env bash
# Remove the production CUPS printer and installed share/bin files.
set -euo pipefail
BACKEND_DST="/usr/lib/cups/backend/ricohlanfax"
SHARE="/usr/local/share/ricoh-lanfax"
PRINTER_NAME="Ricoh-LAN-Fax"
DESKTOP_DST="/usr/share/applications/Ricoh-LAN-Fax.desktop"
BIN_DST="/usr/local/bin/ricoh-lanfax"
PPD_DST="/usr/share/ppd/ricoh/ricoh-lanfax.ppd"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo:  sudo $0"
  exit 1
fi

cancel -a "$PRINTER_NAME" 2>/dev/null || true
if lpstat -p "$PRINTER_NAME" >/dev/null 2>&1; then
  lpadmin -x "$PRINTER_NAME" || true
fi

rm -f "$BACKEND_DST" "$BIN_DST" "$DESKTOP_DST" "$PPD_DST"
rm -rf "$SHARE"

echo "Removed $PRINTER_NAME and installed files under $SHARE."
echo "User config (~/.config/ricoh-lanfax) was left in place."
