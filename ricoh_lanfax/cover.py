"""Blank cover page (send info + optional message) and PNG preview raster."""

from __future__ import annotations

import getpass
import socket
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .send import _gs, raster_to_g4_pages


def _ps_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, width: int = 68) -> list[str]:
    lines: list[str] = []
    for para in text.replace("\r\n", "\n").split("\n"):
        if not para:
            lines.append("")
            continue
        rest = para
        while len(rest) > width:
            cut = rest.rfind(" ", 0, width)
            if cut < 1:
                cut = width
            lines.append(rest[:cut])
            rest = rest[cut:].lstrip()
        lines.append(rest)
    return lines[:45]


@dataclass
class CoverSpec:
    numbers: list[str]
    sender: str
    hostname: str
    title: str
    message: str
    show_info: bool = True
    document_pages: int = 0
    date_s: str = ""
    time_s: str = ""

    def stamp(self) -> "CoverSpec":
        now = datetime.now()
        return replace(
            self,
            date_s=self.date_s or now.strftime("%d.%m.%Y"),
            time_s=self.time_s or now.strftime("%H:%M"),
            sender=self.sender.strip() or getpass.getuser(),
            hostname=self.hostname.strip() or socket.gethostname(),
        )

    def total_label(self) -> str:
        doc = max(0, self.document_pages)
        total = doc + 1
        return f"{total}  (1 Deckblatt + {doc} Dokument)"


def default_sender() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""


def _display_date(date_s: str) -> str:
    """Recipient-facing date: DD.MM.YYYY."""
    raw = date_s.strip()
    if len(raw) >= 10 and raw[4] in "/-":
        try:
            year, month, day = raw[:10].replace("-", "/").split("/")
            return f"{int(day):02d}.{int(month):02d}.{year}"
        except ValueError:
            pass
    return raw


def build_cover_ps(spec: CoverSpec) -> bytes:
    spec = spec.stamp()
    # text, size, bold, underline
    body: list[tuple[str, int, bool, bool]] = [
        ("FAX", 22, True, False),
        ("", 12, False, False),
    ]
    time_bit = spec.time_s[:5] if spec.time_s else ""
    when = _display_date(spec.date_s)
    if time_bit:
        when = f"{when}  {time_bit}"
    if spec.show_info:
        dest = ", ".join(spec.numbers) if spec.numbers else "—"
        body.extend(
            [
                (f"An:      {dest}", 12, False, False),
                (f"Von:     {spec.sender}", 12, False, False),
                (f"Datum:   {when}", 12, False, False),
                (f"Seiten:  {spec.total_label()}", 12, False, False),
                ("", 12, False, False),
            ]
        )
    else:
        body.extend(
            [
                (f"Datum:   {when}", 12, False, False),
                ("", 12, False, False),
            ]
        )
    if spec.message.strip():
        body.append(("Nachricht:", 14, True, True))
        body.append(("", 12, False, False))
        for line in _wrap(spec.message.strip()):
            body.append((line, 12, False, False))

    cmds = [
        "%!PS-Adobe-3.0",
        "%%BoundingBox: 0 0 595 842",
        "%%Pages: 1",
        "%%Page: 1 1",
        "1 setlinewidth",
    ]
    y = 780
    for text, size, bold, underline in body:
        font = "Courier-Bold" if bold else "Courier"
        cmds.append(f"/{font} findfont {size} scalefont setfont")
        cmds.append(f"70 {y} moveto ({_ps_escape(text)}) show")
        if underline and text:
            cmds.append(f"/Courier-Bold findfont {size} scalefont setfont")
            cmds.append(f"70 {y - 3} moveto ({_ps_escape(text)}) stringwidth pop 0 rlineto stroke")
        y -= 18 if size < 18 else 28
        if y < 60:
            break
    cmds.append("showpage")
    cmds.append("%%EOF")
    return ("\n".join(cmds) + "\n").encode("latin-1", errors="replace")


def raster_cover_g4(spec: CoverSpec) -> tuple[int, bytes]:
    with tempfile.TemporaryDirectory(prefix="lanfax-cover-") as tmp:
        path = Path(tmp) / "cover.ps"
        path.write_bytes(build_cover_ps(spec))
        pages = raster_to_g4_pages(path)
        if not pages:
            raise RuntimeError("Deckblatt konnte nicht gerastert werden")
        return pages[0]


def render_preview_pngs(
    document: Path | None,
    cover: CoverSpec | None,
    out_dir: Path,
) -> list[Path]:
    """Render cover (optional) + document pages to PNG files, first page = 1."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("preview-*.png"):
        old.unlink()
    doc_files: list[Path] = []
    if document is not None and document.exists() and document.suffix.lower() != ".raw":
        from .send import materialize_document

        data = materialize_document(document.read_bytes())
        suffix = ".pdf" if data.lstrip().startswith(b"%PDF") else ".ps"
        src = out_dir / f"doc{suffix}"
        src.write_bytes(data)
        doc_files = _gs_png_pages(src, out_dir, "preview-doc", 1)
    files: list[Path] = []
    if cover is not None:
        spec = replace(cover.stamp(), document_pages=len(doc_files))
        ps = out_dir / "cover.ps"
        ps.write_bytes(build_cover_ps(spec))
        files.extend(_gs_png_pages(ps, out_dir, "preview", 1))
    start = len(files) + 1
    for i, src in enumerate(doc_files):
        dest = out_dir / f"preview-{start + i:02d}.png"
        dest.write_bytes(src.read_bytes())
        src.unlink(missing_ok=True)
        files.append(dest)
    return files


def _gs_png_pages(source: Path, out_dir: Path, prefix: str, start: int) -> list[Path]:
    pattern = out_dir / f"{prefix}-tmp-%02d.png"
    cmd = [
        _gs(),
        "-q",
        "-dSAFER",
        "-dNOPAUSE",
        "-dBATCH",
        "-dNOPROMPT",
        "-sDEVICE=pnggray",
        "-r144",
        "-sPAPERSIZE=a4",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        f"-sOutputFile={pattern}",
        str(source),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    produced = sorted(out_dir.glob(f"{prefix}-tmp-*.png"))
    if proc.returncode != 0 or not produced:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Vorschau fehlgeschlagen: {err or proc.returncode}")
    out: list[Path] = []
    for i, src in enumerate(produced):
        dest = out_dir / f"{prefix}-{start + i:02d}.png"
        dest.write_bytes(src.read_bytes())
        src.unlink(missing_ok=True)
        out.append(dest)
    return out
