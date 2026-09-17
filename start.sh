#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "Ricoh LAN-Fax  —  $(pwd)"
echo "Ubuntu printer: sudo ./install-printer.sh"
echo

if [[ "${1:-}" == "--terminal" || "${1:-}" == "-t" ]]; then
  echo "Terminal sink. Point Windows LAN-Fax at Raw port 9100 on this machine."
  echo "IPv4 addresses:"
  ip -4 -br addr 2>/dev/null || hostname -I
  echo
  echo "Ctrl+C stops."
  echo
  exec python3 -m ricoh_lanfax sink --listen 0.0.0.0:9100 --out captures
fi

if python3 -c "import tkinter" 2>/dev/null; then
  exec python3 -m ricoh_lanfax gui
fi

echo "No GUI (python3-tk missing). Starting terminal sink."
echo "Install GUI:     sudo apt install python3-tk"
echo "Terminal only:   ./start.sh --terminal"
echo
exec python3 -m ricoh_lanfax sink --listen 0.0.0.0:9100 --out captures
