"""Expand Windows .dl_/.ex_ (SZDD) and .cfz/.dlz (ZIP), then list protocol strings."""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

INTERESTING = re.compile(
    rb"(RFAX|PC-FAX|PCFAX|@PJL|FAXNUMBER|FAXNUM|LAN-?Fax|ipp://|/ipp|/printer|"
    rb"destination-uri|tel:|ENTER LANGUAGE|9100|PJL SET|UserCode|USERCODE|"
    rb"FAXTEL|FAX_DEST|job-ticket|TIFF)",
    re.IGNORECASE,
)
ASCII_STRING = re.compile(rb"[\x20-\x7e]{6,}")

COMPRESSED_GLOBS = ("*.dl_", "*.ex_", "*.dlz", "*.cfz")
SZDD_MAGIC = b"SZDD\x88\xf0'3"


def list_compressed(disk1: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in COMPRESSED_GLOBS:
        files.extend(sorted(disk1.glob(pattern)))
    return files


def decompress_szdd(data: bytes) -> bytes:
    """Microsoft compress.exe SZDD (LZSS), as used for Windows .dl_ driver files."""
    if not data.startswith(SZDD_MAGIC):
        raise ValueError("not SZDD")
    if len(data) < 14 or data[8] != 0x41:  # mode 'A'
        raise ValueError("unsupported SZDD mode")
    out_len = struct.unpack_from("<I", data, 10)[0]
    src = data[14:]
    window = bytearray(b" " * 4096)
    window_pos = 4096 - 16
    out = bytearray(out_len)
    out_pos = 0
    i = 0
    slen = len(src)

    def take() -> int:
        nonlocal i
        if i >= slen:
            raise ValueError("truncated SZDD stream")
        b = src[i]
        i += 1
        return b

    while out_pos < out_len and i < slen:
        control = take()
        for cb in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80):
            if out_pos >= out_len or i >= slen:
                break
            if control & cb:
                byte = take()
                out[out_pos] = window[window_pos] = byte
                out_pos += 1
                window_pos = (window_pos + 1) & 0xFFF
            else:
                if i + 1 >= slen:
                    break
                match_pos = take()
                match_len = take()
                match_pos |= (match_len & 0xF0) << 4
                match_len = (match_len & 0x0F) + 3
                match_pos &= 0xFFF
                for _ in range(match_len):
                    if out_pos >= out_len:
                        break
                    byte = window[match_pos]
                    window[window_pos] = byte
                    out[out_pos] = byte
                    out_pos += 1
                    window_pos = (window_pos + 1) & 0xFFF
                    match_pos = (match_pos + 1) & 0xFFF
    if out_pos != out_len:
        return bytes(out[:out_pos])
    return bytes(out)


def _try_expand(src: Path, dest: Path) -> bool:
    data = src.read_bytes()
    if data.startswith(b"MZ"):
        dest.write_bytes(data)
        return True
    if data.startswith(SZDD_MAGIC):
        dest.write_bytes(decompress_szdd(data))
        return dest.exists() and dest.stat().st_size > 0
    if data.startswith(b"PK"):
        dest.parent.mkdir(parents=True, exist_ok=True)
        extract_dir = dest.with_suffix(dest.suffix + ".zipdir")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as zf:
            zf.extractall(extract_dir)
            members = [n for n in zf.namelist() if not n.endswith("/")]
        dest.write_text("\n".join(members), encoding="utf-8")
        return True
    return False


def extract_strings(data: bytes) -> list[str]:
    texts = [m.group().decode("ascii", errors="ignore") for m in ASCII_STRING.finditer(data)]
    interesting = []
    for text in texts:
        if INTERESTING.search(text.encode("ascii", errors="ignore")):
            interesting.append(text)
    seen: set[str] = set()
    out: list[str] = []
    for text in interesting:
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _scan_bytes(label: str, data: bytes, lines: list[str], all_hits: list[str]) -> None:
    hits = extract_strings(data)
    if hits:
        lines.append(f"  interesting strings: {len(hits)}")
        for hit in hits[:80]:
            lines.append(f"    {hit}")
            all_hits.append(f"{label}\t{hit}")
        if len(hits) > 80:
            lines.append(f"    ... {len(hits) - 80} more")
    else:
        lines.append("  (no protocol-like ASCII strings)")


def expand_and_scan(disk1: Path, out_dir: Path) -> str:
    lines: list[str] = []
    files = list_compressed(disk1)
    if not files:
        lines.append(f"No compressed driver files in {disk1}")
        lines.append("Expected e.g. rictW0ge.dl_, rictW0lm.dl_, ifxapi64.dl_, rictW0ci.dl_")
        lines.append("Copy the complete DISK1 from the Ricoh installer here, then re-run:")
        lines.append(f"  python3 -m ricoh_lanfax expand-strings {disk1}")
        return "\n".join(lines) + "\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "strings-map.txt"
    all_hits: list[str] = []
    for src in files:
        if src.suffix.lower() == ".dl_":
            dest = out_dir / (src.stem + ".dll")
        elif src.suffix.lower() == ".ex_":
            dest = out_dir / (src.stem + ".exe")
        else:
            dest = out_dir / src.name
        try:
            ok = _try_expand(src, dest)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{src.name}: expand error: {exc}")
            continue
        peek = dest.read_bytes()[:2] if dest.exists() and dest.is_file() else b""
        kind = "PE" if peek == b"MZ" else ("zip listing" if dest.suffix in {".cfz", ".dlz"} or src.suffix.lower() in {".cfz", ".dlz"} else "raw")
        lines.append(f"{src.name}: {'expanded -> ' + dest.name + ' (' + kind + ')' if ok else 'FAILED to expand'}")
        if not ok:
            continue
        if dest.is_file() and dest.suffix.lower() in {".dll", ".exe"}:
            _scan_bytes(src.name, dest.read_bytes(), lines, all_hits)
        elif src.suffix.lower() in {".cfz", ".dlz"}:
            zipdir = dest.with_suffix(dest.suffix + ".zipdir")
            if zipdir.is_dir():
                for nested in sorted(zipdir.rglob("*")):
                    if nested.is_file() and nested.stat().st_size < 20_000_000:
                        _scan_bytes(f"{src.name}:{nested.name}", nested.read_bytes(), lines, all_hits)
            else:
                _scan_bytes(src.name, src.read_bytes(), lines, all_hits)

    map_path.write_text("\n".join(all_hits) + ("\n" if all_hits else ""), encoding="utf-8")
    lines.append(f"wrote {map_path}")
    return "\n".join(lines) + "\n"
