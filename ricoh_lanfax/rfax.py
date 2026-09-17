"""Ricoh RFAX binary records (after @PJL ENTER LANGUAGE=RFAX).

Each record:

    ESC  cmd  group  len_lo  len_hi  payload[len]

Lengths are little-endian. Group 0x01 = job header, 0x02 = destinations,
0x03 = page/image. Names come from the driver's Lua in rictW0cf.cfz.
"""

from __future__ import annotations

from dataclasses import dataclass

ESC = 0x1B

RECORD_NAMES: dict[tuple[int, int], str] = {
    (0x01, 0x01): "DRIVERTYPE",
    (0x02, 0x01): "JOBTYPE",
    (0x03, 0x01): "COVERLETTERPRINT",
    (0x04, 0x01): "COVERLETTER_PAGENUM",
    (0x05, 0x01): "EMAIL_NOTIFY_ADDRESS",
    (0x06, 0x01): "EMAIL_NOTIFY_FLAGS",
    (0x07, 0x01): "LOGIN_USERNAME",
    (0x08, 0x01): "LOGIN_PASSWORD",
    (0x09, 0x01): "SECURITY_OPTION",
    (0x0A, 0x01): "USERAUTH_ENCRYPT",
    (0x0B, 0x01): "SENDER_NUMBER",
    (0x0C, 0x01): "PROTECTION_CODE",
    (0x0D, 0x01): "PINCODE",
    (0x10, 0x01): "USERNAME",
    (0x11, 0x01): "DOCSERVER_FILENAME",
    (0x12, 0x01): "DOCSERVER_PASSWORD",
    (0x13, 0x01): "USERID",
    (0x14, 0x01): "USERCODE",
    (0x15, 0x01): "TRACKID",
    (0x16, 0x01): "HOSTNAME",
    (0x17, 0x01): "LOGINNAME",
    (0x18, 0x01): "DATE",
    (0x19, 0x01): "TIME",
    (0x20, 0x01): "CHARSET",
    (0x21, 0x01): "JOB_OPTION",
    (0x22, 0x01): "FAXHEADER_TYPE",
    (0x23, 0x01): "DOCSERVER_ENCRYPT",
    (0x24, 0x01): "BILLINGCODE",
    (0xF0, 0x01): "EOJ_HEADER",
    (0xF1, 0x01): "EOJ_HEADER2",
    (0xFF, 0x01): "DEST_CHECKSUM",
    (0x00, 0x02): "DEST_COUNT",
    (0x01, 0x02): "DEST_ADDRESS",
    (0x10, 0x02): "SCHEDULED_FAX",
    (0x12, 0x02): "JOBPARAM_OPTION",
    (0xF0, 0x02): "EOJ_PARAM",
    (0x01, 0x03): "PAGE_RESOLUTION",
    (0x02, 0x03): "PAGE_PRINTSIZE",
    (0x03, 0x03): "PAGE_INPUTTRAY",
    (0x04, 0x03): "PAGE_COMPRESS",
    (0x05, 0x03): "PAGE_ROTATION",
    (0x08, 0x03): "IMAGE_LINECOUNT",
    (0xF0, 0x03): "IMAGE_PAGE",
    (0xF1, 0x03): "IMAGE_OTHER",
    (0xF2, 0x03): "END_PHYSICAL_PAGE",
}

JOBTYPE_PAYLOAD = {
    b"@FAX": "send fax",
    b"@PRN": "print copy",
    b"@MEM": "document server",
}

RESOLUTION = {0x00: "200 dpi", 0x01: "400 dpi", 0x02: "200x100", 0x03: "600 dpi"}
PRINTSIZE = {0: "Letter", 1: "A4", 2: "Legal", 3: "B4", 4: "A3", 5: "11x17", 6: "B5", 7: "A5"}
COMPRESS = {0x03: "MMR/G4 (Ricoh compress=3)"}


@dataclass
class RfaxRecord:
    cmd: int
    group: int
    payload: bytes

    @property
    def name(self) -> str:
        return RECORD_NAMES.get((self.cmd, self.group), f"UNKNOWN_{self.cmd:02X}_{self.group:02X}")

    def describe(self) -> str:
        name = self.name
        payload = self.payload
        if name == "JOBTYPE":
            label = JOBTYPE_PAYLOAD.get(payload, repr(payload))
            return f"{name} {label}"
        if name == "DEST_COUNT" and len(payload) >= 2:
            n = payload[0] | (payload[1] << 8)
            return f"{name} = {n}"
        if name == "DEST_ADDRESS" and payload:
            dtype = payload[0]
            addr = payload[1:].decode("latin-1", errors="replace")
            return f"{name} type={dtype} address={addr!r}"
        if name == "DEST_CHECKSUM" and len(payload) >= 2:
            return f"{name} = {payload[0] | (payload[1] << 8)}"
        if name == "PAGE_RESOLUTION" and payload:
            return f"{name} = {RESOLUTION.get(payload[0], hex(payload[0]))}"
        if name == "PAGE_PRINTSIZE" and payload:
            return f"{name} = {PRINTSIZE.get(payload[0], hex(payload[0]))}"
        if name == "PAGE_COMPRESS" and payload:
            return f"{name} = {COMPRESS.get(payload[0], hex(payload[0]))}"
        if name == "IMAGE_LINECOUNT" and len(payload) >= 2:
            return f"{name} = {payload[0] | (payload[1] << 8)}"
        if name in {"IMAGE_PAGE", "IMAGE_OTHER"} and len(payload) >= 4:
            size = int.from_bytes(payload[:4], "little")
            rest = payload[4:]
            return f"{name} declared={size} attached={len(rest)} bytes"
        if name == "DRIVERTYPE":
            return f"{name} {payload.decode('latin-1', errors='replace')!r}"
        textish = payload.decode("latin-1", errors="replace") if payload and all(32 <= b < 127 or b in (9, 10, 13) for b in payload) else None
        if textish is not None and payload:
            return f"{name} {textish!r}"
        if not payload:
            return name
        preview = payload[:24].hex(" ")
        extra = f" … +{len(payload) - 24}B" if len(payload) > 24 else ""
        return f"{name} [{len(payload)}B] {preview}{extra}"


def parse_records(data: bytes) -> tuple[list[RfaxRecord], bytes]:
    """Parse RFAX records. Returns (records, leftover including image blobs)."""
    records: list[RfaxRecord] = []
    i = 0
    n = len(data)
    while i + 5 <= n:
        if data[i] != ESC:
            break
        cmd, group = data[i + 1], data[i + 2]
        if (cmd, group) in {(0xF0, 0x03), (0xF1, 0x03)} and i + 7 <= n:
            size = int.from_bytes(data[i + 3 : i + 7], "little")
            start = i + 7
            end = start + size
            if end > n:
                records.append(RfaxRecord(cmd, group, data[i + 3 :]))
                return records, b""
            records.append(RfaxRecord(cmd, group, data[i + 3 : end]))
            i = end
            continue
        length = data[i + 3] | (data[i + 4] << 8)
        start = i + 5
        end = start + length
        if end > n:
            break
        records.append(RfaxRecord(cmd, group, data[start:end]))
        i = end
    return records, data[i:]


def encode_record(cmd: int, group: int, payload: bytes) -> bytes:
    if (cmd, group) in {(0xF0, 0x03), (0xF1, 0x03)}:
        return bytes([ESC, cmd, group]) + len(payload).to_bytes(4, "little") + payload
    if len(payload) > 0xFFFF:
        raise ValueError("RFAX payload too large")
    return bytes([ESC, cmd, group, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload


def dest_checksum(type_byte: int, address: str) -> int:
    """Sum of DEST_ADDRESS bytes (type + ASCII). Captures use type=0."""
    data = bytes([type_byte]) + address.encode("ascii", errors="replace")
    return sum(data) & 0xFFFF


def format_records(data: bytes) -> str:
    records, rest = parse_records(data)
    lines = [f"rfax_records: {len(records)}"]
    for rec in records:
        lines.append(
            f"  ESC cmd=0x{rec.cmd:02X} group=0x{rec.group:02X} len={len(rec.payload):<5}  {rec.describe()}"
        )
    if rest:
        lines.append(f"  leftover: {len(rest)} bytes  head={rest[:32]!r}")
    return "\n".join(lines) + "\n"
