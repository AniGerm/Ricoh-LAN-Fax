"""Extract every PJL/RFAX fragment from expanded driver files into readable text."""

from __future__ import annotations

import re
from pathlib import Path

PJL_RE = re.compile(rb"@PJL[^\x00-\x08\x0b\x0c\x0e-\x1f]{0,200}")
LUA_PJL_RE = re.compile(r"@PJL[^'\"\\\n]{0,120}")
INFO_KEYS = re.compile(r"node\('([A-Z0-9_]+)'\)")


def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def scan_bytes(data: bytes) -> list[str]:
    hits = []
    for match in PJL_RE.finditer(data):
        text = match.group().decode("latin-1", errors="replace").rstrip()
        if text.startswith("@PJL"):
            hits.append(text)
    return _unique(hits)


def scan_lua(text: str) -> list[str]:
    return _unique(LUA_PJL_RE.findall(text))


def build_catalog(expanded_dir: Path) -> str:
    lines: list[str] = []
    lines.append("Ricoh LAN-Fax PJL / RFAX — extracted from the Windows driver")
    lines.append("=" * 72)
    lines.append("")
    lines.append("Source: DISK1 compressed files, unpacked by `ricoh-lanfax expand-strings`.")
    lines.append("ASCII PJL wraps the job; after ENTER LANGUAGE=RFAX comes a binary ticket.")
    lines.append("")
    lines.append("Job on the wire (Port 9100 RAW)")
    lines.append("-" * 72)
    lines.append("  ESC%-12345X")
    lines.append("  @PJL PCFAXJOB")
    lines.append('  @PJL SET USERID="..."          (optional)')
    lines.append('  @PJL SET USERCODE="..."        (optional)')
    lines.append('  @PJL SET TRACKID="..."         (optional)')
    lines.append('  @PJL SET HOSTLOGINNAME="..."   (optional)')
    lines.append('  @PJL SET HOSTNAME="..."        (optional)')
    lines.append('  @PJL SET BILLINGCODE="..."     (optional)')
    lines.append('  @PJL SET DATE="YYYY/MM/DD"')
    lines.append('  @PJL SET TIME="HH:MM:SS"')
    lines.append('  @PJL COMMENT NOTIFICATION="0x0N"   (optional)')
    lines.append("  @PJL COMMENT PCFAXJOBID=FAX        (send)")
    lines.append("  @PJL COMMENT PCFAXJOBID=PRN        (also print)")
    lines.append("  @PJL COMMENT PCFAXJOBID=MEM        (document server)")
    lines.append("  @PJL SET DRIVERKINDINFO=PCFAXGENERIC")
    lines.append("  @PJL ENTER LANGUAGE=RFAX")
    lines.append("  <binary RFAX records — fax number lives HERE, not in SET FAXNUMBER>")
    lines.append("  ESC%-12345X")
    lines.append("")
    lines.append("RFAX record layout")
    lines.append("-" * 72)
    lines.append("  ESC  cmd  group  len_lo  len_hi  payload[len]     (len is little-endian)")
    lines.append("  group 0x01 job header | 0x02 destinations | 0x03 page/image")
    lines.append("")
    lines.append("  Destination (the phone number):")
    lines.append("    ESC 00 02  02 00  count_lo count_hi     DEST_COUNT")
    lines.append("    ESC 01 02  len_lo len_hi  type  ascii-address")
    lines.append("         len = strlen(address)+1  (type byte + address)")
    lines.append("    ESC FF 01  02 00  sum_lo sum_hi         DEST_CHECKSUM (per dest)")
    lines.append("    ESC F0 01  00 00                        end of header")
    lines.append("    ESC F0 02  00 00                        end of dest params")
    lines.append("")
    lines.append("  Page + image:")
    lines.append("    ESC 01 03  01 00  resolution     0=200, 1=400, 2=200x100, 3=600")
    lines.append("    ESC 04 03  01 00  03             compress = MMR/G4")
    lines.append("    ESC 08 03  02 00  lines_lo lines_hi")
    lines.append("    ESC F0 03  size0..size3          then raster bytes")
    lines.append("    ESC F2 03  00 00                 end physical page")
    lines.append("")
    lines.append("Bidirectional queries (Language Monitor → device)")
    lines.append("-" * 72)
    lines.append("  @PJL INFO RFAX")
    lines.append("  @PJL INFO CONFIG")
    lines.append("  @PJL INFO STOREDPRINTFUNC")
    lines.append("  @PJL INQUIRE G3X1CONFIGURATION   (also G3X2, G3X3, G4X1)")
    lines.append("  Response keys inside INFO RFAX:")

    lua_root = expanded_dir / "rictW0cf.cfz.zipdir"
    keys: list[str] = []
    if lua_root.is_dir():
        pjl_lua = lua_root / "script/lua/iGanay/common/rule/pjl.lua"
        if pjl_lua.exists():
            keys = _unique(INFO_KEYS.findall(pjl_lua.read_text(encoding="utf-8", errors="replace")))
    for key in keys:
        lines.append(f"    {key}")
    lines.append("")

    lines.append("Raw @PJL strings found in unpacked files")
    lines.append("-" * 72)
    found: list[tuple[str, str]] = []
    if expanded_dir.is_dir():
        for path in sorted(expanded_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.stat().st_size > 20_000_000:
                continue
            if path.suffix.lower() in {".dll", ".exe", ".lua", ".txt"} or path.name.endswith(".lua"):
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                rel = str(path.relative_to(expanded_dir))
                if path.suffix.lower() == ".lua":
                    for hit in scan_lua(data.decode("latin-1", errors="replace")):
                        found.append((rel, hit))
                else:
                    for hit in scan_bytes(data):
                        found.append((rel, hit))
    # de-dupe by command text, keep first file
    seen_cmd: set[str] = set()
    for rel, hit in found:
        if hit in seen_cmd:
            continue
        seen_cmd.add(hit)
        lines.append(f"  {hit}")
        lines.append(f"      ← {rel}")
    if not found:
        lines.append("  (run: python3 -m ricoh_lanfax expand-strings DISK1 --out captures/expanded)")
    lines.append("")
    return "\n".join(lines) + "\n"
