"""Parse RAW printer streams: UEL, PJL, and RFAX/binary payload."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

UEL = b"\x1b%-12345X"
PHONE_RE = re.compile(rb"(?<![0-9])[+0-9][0-9P#*\- ]{5,40}(?![0-9])")


@dataclass
class PjlCommand:
    raw: bytes
    text: str


@dataclass
class JobSection:
    pjl: list[PjlCommand] = field(default_factory=list)
    language: str | None = None
    payload: bytes = b""

    @property
    def payload_looks_like_tiff(self) -> bool:
        return self.payload.startswith(b"II*\x00") or self.payload.startswith(b"MM\x00*")


@dataclass
class ParsedJob:
    raw: bytes
    sections: list[JobSection] = field(default_factory=list)
    trailing: bytes = b""

    @property
    def pjl_commands(self) -> list[PjlCommand]:
        out: list[PjlCommand] = []
        for section in self.sections:
            out.extend(section.pjl)
        return out

    @property
    def payload(self) -> bytes:
        parts = [s.payload for s in self.sections if s.payload]
        return b"".join(parts)

    @property
    def language(self) -> str | None:
        for section in self.sections:
            if section.language:
                return section.language
        return None


def _decode_pjl_line(line: bytes) -> str:
    text = line.decode("latin-1", errors="replace").rstrip("\r\n")
    return text


def _parse_segment(segment: bytes) -> JobSection:
    section = JobSection()
    if not segment:
        return section

    pos = 0
    while pos < len(segment) and segment[pos] in (10, 13, 0):
        pos += 1
    if not segment[pos:].upper().startswith(b"@PJL"):
        section.payload = segment
        return section

    while pos < len(segment):
        nl = segment.find(b"\n", pos)
        if nl < 0:
            line = segment[pos:]
            next_pos = len(segment)
        else:
            line = segment[pos : nl + 1]
            next_pos = nl + 1
        stripped = line.strip(b"\r\n")
        if not stripped:
            pos = next_pos
            continue
        if stripped.upper().startswith(b"@PJL"):
            text = _decode_pjl_line(line)
            section.pjl.append(PjlCommand(raw=line, text=text))
            pos = next_pos
            enter = re.match(
                r"@PJL\s+ENTER\s+LANGUAGE\s*=\s*([A-Za-z0-9_-]+)",
                text,
                re.IGNORECASE,
            )
            if enter:
                section.language = enter.group(1).upper()
                section.payload = segment[pos:]
                return section
            continue
        section.payload = segment[pos:]
        return section
    return section


def parse_job(data: bytes) -> ParsedJob:
    """Split a RAW 9100 stream on UEL and extract PJL vs payload."""
    job = ParsedJob(raw=data)
    if not data:
        return job
    if UEL not in data:
        job.sections.append(_parse_segment(data))
        return job

    parts = data.split(UEL)
    if parts[0]:
        job.sections.append(_parse_segment(parts[0]))
    for part in parts[1:]:
        if part:
            job.sections.append(_parse_segment(part))
    return job


def format_report(job: ParsedJob) -> str:
    lines: list[str] = []
    lines.append(f"bytes: {len(job.raw)}")
    lines.append(f"uel_count: {job.raw.count(UEL)}")
    lines.append(f"language: {job.language or '(none)'}")
    lines.append(f"pjl_commands: {len(job.pjl_commands)}")
    lines.append(f"payload_bytes: {len(job.payload)}")
    if job.payload:
        preview = job.payload[:32]
        lines.append(f"payload_head: {preview!r}")
        tiff = any(s.payload_looks_like_tiff for s in job.sections)
        lines.append(f"payload_tiff_header: {tiff}")
    phones = find_phone_like(job.raw)
    if phones:
        lines.append("phone_like:")
        for off, token in phones:
            lines.append(f"  @{off}: {token}")
    if job.language == "RFAX" and job.payload:
        from .rfax import format_records

        lines.append("")
        lines.append("=== RFAX records ===")
        lines.append(format_records(job.payload).rstrip())
    lines.append("")
    lines.append("=== PJL ===")
    if not job.pjl_commands:
        lines.append("(no ASCII @PJL commands found)")
    for cmd in job.pjl_commands:
        lines.append(cmd.text)
    for i, section in enumerate(job.sections):
        if not section.payload:
            continue
        lines.append("")
        lines.append(f"=== payload section {i} ({section.language or 'unknown'}, {len(section.payload)} bytes) ===")
        lines.append(hexdump(section.payload[:256]))
        if len(section.payload) > 256:
            lines.append(f"... ({len(section.payload) - 256} more bytes)")
    return "\n".join(lines) + "\n"


def find_phone_like(data: bytes) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for match in PHONE_RE.finditer(data):
        token = match.group().decode("ascii", errors="replace").strip()
        digits = re.sub(r"\D", "", token)
        if len(digits) < 6:
            continue
        found.append((match.start(), token))
    return found


def hexdump(data: bytes, width: int = 16) -> str:
    rows: list[str] = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        rows.append(f"{offset:08x}  {hex_part:<{width * 3}} {ascii_part}")
    return "\n".join(rows)


def diff_jobs(a: bytes, b: bytes, label_a: str = "a", label_b: str = "b") -> str:
    """Show byte ranges that differ and printable strings unique to each job."""
    lines: list[str] = []
    lines.append(f"{label_a}: {len(a)} bytes")
    lines.append(f"{label_b}: {len(b)} bytes")
    max_len = max(len(a), len(b))
    start: int | None = None
    ranges: list[tuple[int, int]] = []
    for i in range(max_len):
        va = a[i] if i < len(a) else None
        vb = b[i] if i < len(b) else None
        if va != vb:
            if start is None:
                start = i
        elif start is not None:
            ranges.append((start, i))
            start = None
    if start is not None:
        ranges.append((start, max_len))

    lines.append(f"differing_ranges: {len(ranges)}")
    for start, end in ranges[:40]:
        chunk_a = a[start:end]
        chunk_b = b[start:end]
        lines.append(f"  [{start}:{end}] ({end - start} bytes)")
        lines.append(f"    {label_a}: {chunk_a[:64]!r}")
        lines.append(f"    {label_b}: {chunk_b[:64]!r}")
    if len(ranges) > 40:
        lines.append(f"  ... {len(ranges) - 40} more ranges")

    pa = {t for _, t in find_phone_like(a)}
    pb = {t for _, t in find_phone_like(b)}
    only_a = sorted(pa - pb)
    only_b = sorted(pb - pa)
    lines.append("")
    lines.append("phone_like only in first job:" if only_a else "phone_like only in first job: (none)")
    for item in only_a:
        lines.append(f"  {item}")
    lines.append("phone_like only in second job:" if only_b else "phone_like only in second job: (none)")
    for item in only_b:
        lines.append(f"  {item}")

    parsed_a = parse_job(a)
    parsed_b = parse_job(b)
    cmds_a = [c.text for c in parsed_a.pjl_commands]
    cmds_b = [c.text for c in parsed_b.pjl_commands]
    if cmds_a != cmds_b:
        lines.append("")
        lines.append("PJL command list differs:")
        import difflib

        for line in difflib.unified_diff(cmds_a, cmds_b, fromfile=label_a, tofile=label_b, lineterm=""):
            lines.append(line)
    return "\n".join(lines) + "\n"


def replace_number(data: bytes, old: str, new: str) -> tuple[bytes, int]:
    """Replace ASCII fax number tokens. Returns (new_bytes, count)."""
    old_b = old.encode("ascii")
    new_b = new.encode("ascii")
    count = data.count(old_b)
    if count:
        return data.replace(old_b, new_b), count
    # Also try without spaces/dashes in a simple pass over phone-like tokens.
    out = data
    n = 0
    for offset, token in reversed(find_phone_like(data)):
        digits = re.sub(r"\D", "", token)
        old_digits = re.sub(r"\D", "", old)
        if digits == old_digits or token == old:
            encoded = token.encode("ascii")
            out = out[:offset] + new_b + out[offset + len(encoded) :]
            n += 1
    return out, n
