#!/usr/bin/env bash
# Build an amd64/all .deb that installs the CUPS printer + fax UI (no sink app).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
# shellcheck source=/dev/null
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); from ricoh_lanfax import __version__; print(__version__)")"
PKG_NAME="ricoh-lanfax"
ARCH="all"
DIST="$ROOT/dist"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/ricoh-lanfax-deb.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

echo "Building ${PKG_NAME}_${VERSION}_${ARCH}.deb"

mkdir -p \
  "$STAGE/DEBIAN" \
  "$STAGE/usr/local/share/ricoh-lanfax" \
  "$STAGE/usr/local/bin" \
  "$STAGE/usr/lib/cups/backend" \
  "$STAGE/usr/share/ppd/ricoh" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/doc/$PKG_NAME"

cp -a "$ROOT/ricoh_lanfax" "$STAGE/usr/local/share/ricoh-lanfax/ricoh_lanfax"
find "$STAGE/usr/local/share/ricoh-lanfax" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Settings menu yes; lab sink launcher (start.sh) no.
install -m 755 "$ROOT/ricoh-lanfax" "$STAGE/usr/local/bin/ricoh-lanfax"
install -m 700 "$ROOT/cups/ricohlanfax" "$STAGE/usr/lib/cups/backend/ricohlanfax"
install -m 644 "$ROOT/cups/ricoh-lanfax.ppd" "$STAGE/usr/share/ppd/ricoh/ricoh-lanfax.ppd"
install -m 644 "$ROOT/Ricoh-LAN-Fax.desktop" "$STAGE/usr/share/applications/Ricoh-LAN-Fax.desktop"
install -m 644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG_NAME/copyright"
install -m 644 "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG_NAME/README.md"

cat >"$STAGE/DEBIAN/control" <<EOF
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.11), python3-tk, python3-pil, ghostscript, cups, cups-client, cups-bsd
Maintainer: Ricoh LAN-Fax contributors <noreply@example.com>
Homepage: https://github.com/AniGerm/Ricoh-LAN-Fax
 Description: Linux CUPS LAN-Fax printer for Ricoh IM 350F
 Unofficial CUPS printer that opens a fax number dialog (phonebook,
 cover page, preview) and sends a Windows-compatible RAW job to TCP 9100.
 Includes an Ubuntu app-menu entry for printer IP/port and phonebook.
 The Windows capture sink / lab GUI is not included.
EOF

cat >"$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e
PRINTER_NAME="Ricoh-LAN-Fax"
SPOOL="/var/tmp/ricoh-lanfax"
BACKEND="/usr/lib/cups/backend/ricohlanfax"

mkdir -p "$SPOOL/spool"
chmod 1777 "$SPOOL" "$SPOOL/spool"
chmod 700 "$BACKEND" || true
chown root:root "$BACKEND" || true

if command -v lpadmin >/dev/null 2>&1; then
  cancel -a "$PRINTER_NAME" 2>/dev/null || true
  if lpstat -p "$PRINTER_NAME" >/dev/null 2>&1; then
    lpadmin -x "$PRINTER_NAME" || true
  fi
  lpadmin -p "$PRINTER_NAME" -E -v ricohlanfax:/ \
    -P /usr/share/ppd/ricoh/ricoh-lanfax.ppd \
    -D "Ricoh LAN-Fax (number popup)" -L "IM 350F via Linux"
  cupsenable "$PRINTER_NAME" 2>/dev/null || true
  cupsaccept "$PRINTER_NAME" 2>/dev/null || true
fi

echo "ricoh-lanfax: printer '$PRINTER_NAME' ready; menu entry „Ricoh LAN-Fax“ for IP/phonebook."
EOF

cat >"$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/bash
set -e
PRINTER_NAME="Ricoh-LAN-Fax"
if command -v cancel >/dev/null 2>&1; then
  cancel -a "$PRINTER_NAME" 2>/dev/null || true
fi
if command -v lpstat >/dev/null 2>&1 && lpstat -p "$PRINTER_NAME" >/dev/null 2>&1; then
  lpadmin -x "$PRINTER_NAME" || true
fi
EOF

cat >"$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/bash
set -e
# Spool/logs are left for debugging; user config stays in ~/.config/ricoh-lanfax
exit 0
EOF

chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

mkdir -p "$DIST"
OUT="$DIST/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "Wrote $OUT"
dpkg-deb --info "$OUT" | sed -n '1,20p'
dpkg-deb --contents "$OUT" | grep -E 'start\.sh|Ricoh-LAN-Fax\.desktop|ricohlanfax|phonebook' || true
# Fail the build if the lab sink launcher leaked into the package.
if dpkg-deb --contents "$OUT" | grep -E 'start\.sh' >/dev/null; then
  echo "ERROR: lab start.sh found in package" >&2
  exit 1
fi
if ! dpkg-deb --contents "$OUT" | grep -E 'Ricoh-LAN-Fax\.desktop' >/dev/null; then
  echo "ERROR: settings desktop entry missing from package" >&2
  exit 1
fi
TMP_DESK="$(mktemp)"
dpkg-deb --fsys-tarfile "$OUT" | tar -xO ./usr/share/applications/Ricoh-LAN-Fax.desktop >"$TMP_DESK"
if ! grep -q 'ricoh-lanfax settings' "$TMP_DESK"; then
  echo "ERROR: desktop entry does not launch settings" >&2
  rm -f "$TMP_DESK"
  exit 1
fi
rm -f "$TMP_DESK"
