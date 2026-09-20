"""Build and send a LAN-Fax RAW job to TCP 9100."""

from __future__ import annotations

import getpass
import logging
import os
import socket
import struct
import shutil
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .pjl import UEL, parse_job
from .rfax import RfaxRecord, dest_checksum, encode_record, parse_records

RFAX_LANGUAGE = "RFAX"
# Win11 LAN-Fax Generic A4 @ 200 dpi (IMAGE_LINECOUNT in live captures).
FAX_WIDTH_PX = 1728
FAX_HEIGHT_PX = 2259
CUPS_TESTPAGE = Path("/usr/share/cups/data/default-testpage.pdf")
log = logging.getLogger(__name__)


def _ascii(text: str) -> bytes:
    return text.encode("ascii", errors="replace")


def encode_parsed_record(rec: RfaxRecord) -> bytes:
    payload = rec.payload
    if rec.name in {"IMAGE_PAGE", "IMAGE_OTHER"} and len(payload) >= 4:
        declared = int.from_bytes(payload[:4], "little")
        if declared == len(payload) - 4:
            payload = payload[4:]
    return encode_record(rec.cmd, rec.group, payload)


def patch_job_number(raw: bytes, number: str, dest_type: int = 0) -> bytes:
    """Rewrite DEST_ADDRESS + DEST_CHECKSUM in a captured Windows job."""
    job = parse_job(raw)
    if not job.sections or not job.sections[0].payload:
        raise RuntimeError("Template has no RFAX payload")
    payload = job.sections[0].payload
    recs, rest = parse_records(payload)
    if rest:
        raise RuntimeError(f"Unparsed RFAX leftover ({len(rest)} bytes)")
    dest_payload = bytes([dest_type & 0xFF]) + _ascii(number)
    csum = dest_checksum(dest_type, number)
    found = False
    out: list[bytes] = []
    for rec in recs:
        if rec.name == "DEST_ADDRESS":
            rec = RfaxRecord(0x01, 0x02, dest_payload)
            found = True
        elif rec.name == "DEST_CHECKSUM":
            rec = RfaxRecord(0xFF, 0x01, bytes([csum & 0xFF, (csum >> 8) & 0xFF]))
        out.append(encode_parsed_record(rec))
    if not found:
        raise RuntimeError("Template has no DEST_ADDRESS record")
    new_payload = b"".join(out)
    idx = raw.find(payload)
    if idx < 0:
        raise RuntimeError("Could not relocate RFAX payload in template")
    return raw[:idx] + new_payload + raw[idx + len(payload) :]


def wrap_pages(
    pages: list[tuple[int, bytes]],
    number: str,
    dest_type: int = 0,
    extra_pjl: list[str] | None = None,
    hostname: str | None = None,
    loginname: str | None = None,
) -> bytes:
    """Windows-shaped PJL + RFAX ticket around raw MMR page strips."""
    if not pages:
        raise RuntimeError("no pages to send")
    now = datetime.now()
    host = hostname or socket.gethostname()
    user = loginname or getpass.getuser()
    track = now.strftime("L%y%m%d%H%M%S")[:15].ljust(15, "0")
    date_s = now.strftime("%Y/%m/%d")
    time_s = now.strftime("%H:%M:%S")

    pjl = [
        "@PJL PCFAXJOB",
        '@PJL SET BILLINGCODE=""',
        f'@PJL SET HOSTNAME="{host}"',
        '@PJL SET USERID=""',
        '@PJL SET USERCODE=""',
        f'@PJL SET HOSTLOGINNAME="{user}"',
        f'@PJL SET TRACKID="{track}"',
        f'@PJL SET DATE="{date_s}"',
        f'@PJL SET TIME="{time_s}"',
        "@PJL COMMENT PCFAXJOBID=FAX",
        "@PJL SET DRIVERKINDINFO=PCFAXGENERIC",
    ]
    if extra_pjl:
        pjl.extend(extra_pjl)
    pjl.append(f"@PJL ENTER LANGUAGE={RFAX_LANGUAGE}")
    header = UEL + ("\n".join(pjl) + "\n").encode("ascii", errors="replace")

    dest_payload = bytes([dest_type & 0xFF]) + _ascii(number)
    csum = dest_checksum(dest_type, number)
    recs: list[bytes] = [
        encode_record(0x01, 0x01, b"imagio-Z1"),
        encode_record(0x02, 0x01, b"@FAX"),
        encode_record(0x03, 0x01, b"\x00"),
        encode_record(0x06, 0x01, b"\x00"),
        encode_record(0x24, 0x01, b""),
        encode_record(0x09, 0x01, b"\x00"),
        encode_record(0x0B, 0x01, b""),
        encode_record(0x0C, 0x01, b""),
        encode_record(0x0D, 0x01, b""),
        encode_record(0x13, 0x01, b""),
        encode_record(0x14, 0x01, b""),
        encode_record(0x15, 0x01, _ascii(track)),
        encode_record(0x16, 0x01, _ascii(host)[:32]),
        encode_record(0x17, 0x01, _ascii(user)[:32]),
        encode_record(0x18, 0x01, _ascii(date_s)),
        encode_record(0x19, 0x01, _ascii(time_s)),
        encode_record(0x20, 0x01, b"\x01"),
        encode_record(0x21, 0x01, b"\x02"),
        encode_record(0x22, 0x01, b"\x01"),
        encode_record(0xF0, 0x01, b""),
        encode_record(0xF1, 0x01, b""),
        encode_record(0xFF, 0x01, bytes([csum & 0xFF, (csum >> 8) & 0xFF])),
        encode_record(0x00, 0x02, bytes([1, 0])),
        encode_record(0x01, 0x02, dest_payload),
        encode_record(0x10, 0x02, b"\x00"),
        encode_record(0x12, 0x02, b"\x01"),
        encode_record(0xF0, 0x02, b""),
    ]
    for linecount, mmr in pages:
        recs.extend(
            [
                encode_record(0x01, 0x03, b"\x00"),  # 200 dpi
                encode_record(0x02, 0x03, b"\x01"),  # A4
                encode_record(0x03, 0x03, b"\x00"),
                encode_record(0x04, 0x03, b"\x03"),  # MMR/G4
                encode_record(0x05, 0x03, b"\x00"),
                encode_record(0x08, 0x03, bytes([linecount & 0xFF, (linecount >> 8) & 0xFF])),
                encode_record(0xF0, 0x03, mmr),
            ]
        )
    recs.append(encode_record(0x01, 0x04, b""))
    return header + b"".join(recs) + UEL


def wrap_pjl_rfax(
    payload: bytes,
    number: str,
    job_name: str = "linux-lanfax",
    extra_pjl: list[str] | None = None,
    dest_type: int = 0,
) -> bytes:
    _ = job_name
    return wrap_pages([(0, payload)], number, dest_type=dest_type, extra_pjl=extra_pjl)


def _ifd_value(entry: bytes, data: bytes, typ: int, count: int) -> list[int]:
    type_size = {1: 1, 2: 1, 3: 2, 4: 4}.get(typ, 4)
    nbytes = type_size * count
    packed = entry[8:12]
    blob = packed if nbytes <= 4 else data[struct.unpack_from("<I", packed)[0] : struct.unpack_from("<I", packed)[0] + nbytes]
    out: list[int] = []
    for i in range(count):
        chunk = blob[i * type_size : (i + 1) * type_size]
        if typ == 3:
            out.append(int.from_bytes(chunk, "little"))
        elif typ == 4:
            out.append(int.from_bytes(chunk, "little"))
        else:
            out.append(chunk[0] if chunk else 0)
    return out


def tiff_g4_pages(data: bytes) -> list[tuple[int, bytes]]:
    """Return (height, raw Group-4 strip) for each page of a little-endian TIFF."""
    if len(data) < 8 or not data.startswith(b"II*\x00"):
        raise RuntimeError("Not a little-endian TIFF (expected II*)")
    pages: list[tuple[int, bytes]] = []
    next_ifd = struct.unpack_from("<I", data, 4)[0]
    seen: set[int] = set()
    while next_ifd and next_ifd not in seen:
        seen.add(next_ifd)
        count = struct.unpack_from("<H", data, next_ifd)[0]
        tags: dict[int, tuple[int, int, bytes]] = {}
        pos = next_ifd + 2
        for _ in range(count):
            entry = data[pos : pos + 12]
            code, typ, cnt = struct.unpack_from("<HHI", entry, 0)
            tags[code] = (typ, cnt, entry)
            pos += 12
        next_ifd = struct.unpack_from("<I", data, pos)[0]
        length = _ifd_value(tags[257][2], data, tags[257][0], tags[257][1])[0] if 257 in tags else 0
        compression = _ifd_value(tags[259][2], data, tags[259][0], tags[259][1])[0] if 259 in tags else 1
        if compression != 4:
            raise RuntimeError(f"TIFF compression {compression} is not CCITT G4 (4)")
        offsets = _ifd_value(tags[273][2], data, tags[273][0], tags[273][1]) if 273 in tags else []
        sizes = _ifd_value(tags[279][2], data, tags[279][0], tags[279][1]) if 279 in tags else []
        if not offsets or not sizes or len(offsets) != len(sizes):
            raise RuntimeError("TIFF G4 strip tags missing")
        strip = b"".join(data[o : o + n] for o, n in zip(offsets, sizes))
        pages.append((length, strip))
    if not pages:
        raise RuntimeError("TIFF has no pages")
    return pages


def _gs() -> str:
    gs = shutil.which("gs") or shutil.which("ghostscript")
    if not gs:
        raise RuntimeError("Ghostscript (gs) fehlt. sudo apt install ghostscript")
    return gs


def is_cups_banner(data: bytes) -> bool:
    head = data.lstrip()[:40]
    return (
        head.startswith(b"#PDF-BANNER")
        or head.startswith(b"#CUPS-BANNER")
        or head.startswith(b"Template default-testpage")
    )


TEST_PAGE_PS = """%!PS-Adobe-3.0
%%BoundingBox: 0 0 595 842
%%Pages: 1
%%Page: 1 1
/Courier findfont 24 scalefont setfont
70 760 moveto (Ricoh LAN-Fax) show
/Courier findfont 14 scalefont setfont
70 720 moveto (Ubuntu CUPS test page) show
showpage
%%EOF
"""


def materialize_document(data: bytes) -> bytes:
    """Turn a CUPS #PDF-BANNER job into a real PDF/PS Ghostscript can rasterize."""
    if not is_cups_banner(data):
        return data
    try:
        if CUPS_TESTPAGE.exists():
            pdf = CUPS_TESTPAGE.read_bytes()
            if pdf.startswith(b"%PDF"):
                return pdf
    except OSError:
        pass
    return TEST_PAGE_PS.encode("ascii")


def _gs_tiffg4(source: Path, out: Path, resolution: str) -> None:
    cmd = [
        _gs(),
        "-q",
        "-dSAFER",
        "-dNOPAUSE",
        "-dBATCH",
        "-dNOPROMPT",
        "-sDEVICE=tiffg4",
        f"-r{resolution}",
        f"-g{FAX_WIDTH_PX}x{FAX_HEIGHT_PX}",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        f"-sOutputFile={out}",
        str(source),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "CUPS-Testseite/PDF konnte nicht gerastert werden "
            f"(Ghostscript {proc.returncode}): {err}"
        )


def raster_to_g4_pages(source: Path, resolution: str = "200") -> list[tuple[int, bytes]]:
    """PDF/PS/TIFF/CUPS-banner -> list of (linecount, raw MMR/G4)."""
    suffix = source.suffix.lower()
    data = source.read_bytes() if source.exists() else b""
    if suffix in {".tif", ".tiff"}:
        try:
            return tiff_g4_pages(data)
        except RuntimeError:
            return _rewrite_tiff_g4(data, resolution)
    if suffix == ".g4":
        return [(FAX_HEIGHT_PX, data)]

    data = materialize_document(data)
    if is_cups_banner(data):
        # materialize failed — never hand the banner to Ghostscript
        data = TEST_PAGE_PS.encode("ascii")

    with tempfile.TemporaryDirectory(prefix="lanfax-") as tmp:
        tmp_path = Path(tmp)
        if data.lstrip().startswith(b"%PDF"):
            work = tmp_path / "in.pdf"
        elif data.lstrip().startswith(b"%!"):
            work = tmp_path / "in.ps"
        else:
            work = tmp_path / "in.pdf"
        work.write_bytes(data)
        out = tmp_path / "page.tif"
        _gs_tiffg4(work, out, resolution)
        return tiff_g4_pages(out.read_bytes())


def _rewrite_tiff_g4(data: bytes, resolution: str) -> list[tuple[int, bytes]]:
    with tempfile.TemporaryDirectory(prefix="lanfax-") as tmp:
        src = Path(tmp) / "in.tif"
        out = Path(tmp) / "out.tif"
        src.write_bytes(data)
        _gs_tiffg4(src, out, resolution)
        return tiff_g4_pages(out.read_bytes())


def raster_to_tiffg4(source: Path, resolution: str = "200") -> bytes:
    """Back-compat: first page raw G4 (not a TIFF container)."""
    pages = raster_to_g4_pages(source, resolution=resolution)
    return pages[0][1]


def build_job(
    source: Path | None,
    number: str,
    template: Path | None = None,
    replace_from: str | None = None,
    resolution: str = "200",
) -> tuple[bytes, str]:
    """Return (job_bytes, note). Template mode rewrites dest+checksum in a capture."""
    _ = replace_from
    if template is not None:
        data = template.read_bytes()
        job = patch_job_number(data, number)
        note = f"template {template.name}: DEST_ADDRESS + checksum -> {number}"
        return job, note

    if source is None:
        raise RuntimeError("PDF/TIFF angeben oder --template captures/….raw")
    pages = raster_to_g4_pages(source, resolution=resolution)
    job = wrap_pages(pages, number)
    note = (
        f"Linux-Job: {len(pages)} Seite(n) MMR/G4, A4, 200 dpi, Nummer {number}, "
        f"Zeilen {[h for h, _ in pages]}"
    )
    return job, note


def probe_printer(host: str, port: int = 9100, timeout: float = 3.0) -> str:
    if not host.strip():
        raise RuntimeError("Keine Drucker-IP")
    with socket.create_connection((host.strip(), port), timeout=timeout):
        pass
    return f"{host.strip()}:{port} erreichbar"


def write_debug_dump(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def send_document(
    source: Path,
    numbers: list[str],
    host: str,
    port: int = 9100,
    dump_dir: Path | None = None,
    cover: object | None = None,
) -> str:
    """Rasterize a printed PDF/TIFF and send one RFAX job per destination number."""
    if not numbers:
        raise RuntimeError("Keine Faxnummer")
    if not host.strip():
        raise RuntimeError("Keine Drucker-IP — Zahnrad öffnen und IP eintragen")
    doc_pages = raster_to_g4_pages(source)
    notes: list[str] = []
    last = b""
    for number in numbers:
        pages = list(doc_pages)
        if cover is not None:
            from .cover import CoverSpec, raster_cover_g4

            spec = cover
            if isinstance(spec, CoverSpec):
                spec = replace(spec.stamp(), numbers=[number], document_pages=len(doc_pages))
                pages = [raster_cover_g4(spec), *doc_pages]
        job = wrap_pages(pages, number)
        last = job
        send_raw(host.strip(), port, job)
        notes.append(f"{number} ({len(job)} B)")
    if dump_dir is not None and last:
        write_debug_dump(dump_dir / "linux-last.raw", last)
    extra = " inkl. Deckblatt" if cover is not None else ""
    return f"{len(doc_pages) + (1 if cover is not None else 0)} Seite(n){extra} → {host.strip()}:{port}: " + ", ".join(notes)


def send_raw(host: str, port: int, data: bytes, timeout: float = 30.0) -> None:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(data)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
