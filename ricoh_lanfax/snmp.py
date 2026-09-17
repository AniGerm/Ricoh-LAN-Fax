"""Minimal SNMPv1 responder so Windows may identify a Ricoh-like device."""

from __future__ import annotations

import socket
import threading
from typing import Callable

# sysDescr.0, sysObjectID.0, sysName.0, sysLocation.0
SYS_DESCR = (1, 3, 6, 1, 2, 1, 1, 1, 0)
SYS_OBJECT_ID = (1, 3, 6, 1, 2, 1, 1, 2, 0)
SYS_NAME = (1, 3, 6, 1, 2, 1, 1, 5, 0)
SYS_LOCATION = (1, 3, 6, 1, 2, 1, 1, 6, 0)
# HOST-RESOURCES / printer-ish; Ricoh private enterprise 367
RICOH_ENTERPRISE = (1, 3, 6, 1, 4, 1, 367, 1, 1)


def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _ber(tag: int, data: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(data)) + data


def _ber_null() -> bytes:
    return b"\x05\x00"


def _ber_int(value: int, tag: int = 0x02) -> bytes:
    if value == 0:
        body = b"\x00"
    else:
        length = max(1, (value.bit_length() + 8) // 8)
        body = value.to_bytes(length, "big", signed=True)
        if value > 0 and body[0] & 0x80:
            body = b"\x00" + body
    return _ber(tag, body)


def _ber_octet(text: str) -> bytes:
    return _ber(0x04, text.encode("utf-8"))


def _ber_oid(oid: tuple[int, ...]) -> bytes:
    if len(oid) < 2:
        raise ValueError("OID too short")
    body = bytes([40 * oid[0] + oid[1]])
    for n in oid[2:]:
        if n < 0:
            raise ValueError("negative OID")
        stack = [n & 0x7F]
        n >>= 7
        while n:
            stack.append(0x80 | (n & 0x7F))
            n >>= 7
        body += bytes(reversed(stack))
    return _ber(0x06, body)


def _decode_len(data: bytes, i: int) -> tuple[int, int]:
    first = data[i]
    i += 1
    if first < 0x80:
        return first, i
    n = first & 0x7F
    value = int.from_bytes(data[i : i + n], "big")
    return value, i + n


def _decode_tlv(data: bytes, i: int) -> tuple[int, bytes, int]:
    tag = data[i]
    length, j = _decode_len(data, i + 1)
    return tag, data[j : j + length], j + length


def _decode_oid(body: bytes) -> tuple[int, ...]:
    if not body:
        return ()
    first = body[0]
    oid = [first // 40, first % 40]
    n = 0
    for b in body[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            oid.append(n)
            n = 0
    return tuple(oid)


def extract_oids(packet: bytes) -> list[tuple[int, ...]]:
    oids: list[tuple[int, ...]] = []

    def walk(data: bytes) -> None:
        i = 0
        while i < len(data):
            try:
                tag, body, i = _decode_tlv(data, i)
            except (IndexError, ValueError):
                break
            if tag == 0x06:
                oids.append(_decode_oid(body))
            elif tag in (0x30, 0xA0, 0xA1, 0xA2, 0xA3):
                walk(body)

    try:
        walk(packet)
    except Exception:
        return oids
    return oids


def build_get_response(
    request: bytes,
    values: dict[tuple[int, ...], tuple[str, object]],
    sys_descr: str,
    sys_name: str,
) -> bytes | None:
    """Build a GetResponse. Falls back to wrapping community + varbinds if parse is shallow."""
    oids = extract_oids(request)
    if not oids:
        oids = [SYS_DESCR]

    varbinds = b""
    for oid in oids:
        if oid == SYS_DESCR:
            value = _ber_octet(sys_descr)
        elif oid == SYS_NAME:
            value = _ber_octet(sys_name)
        elif oid == SYS_LOCATION:
            value = _ber_octet("LAN-Fax capture sink")
        elif oid == SYS_OBJECT_ID:
            value = _ber_oid(RICOH_ENTERPRISE)
        else:
            # noSuchObject-ish: still return sysDescr so discovery tools see a device
            value = _ber_octet(sys_descr) if oid[:6] == (1, 3, 6, 1, 2, 1) else _ber_null()
        varbinds += _ber(0x30, _ber_oid(oid) + value)

    varbind_list = _ber(0x30, varbinds)
    pdu = _ber(0xA2, _ber_int(1) + _ber_int(0) + _ber_int(0) + varbind_list)
    community = _ber_octet("public")
    message = _ber(0x30, _ber_int(0) + community + pdu)
    return message


class SnmpSink(threading.Thread):
    def __init__(
        self,
        bind_ip: str,
        port: int,
        sys_descr: str,
        sys_name: str,
        log: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True, name="snmp-sink")
        self.bind_ip = bind_ip
        self.port = port
        self.sys_descr = sys_descr
        self.sys_name = sys_name
        self.log = log
        self._halt = threading.Event()
        self.sock: socket.socket | None = None

    def stop(self) -> None:
        self._halt.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.bind_ip, self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            self.log(f"SNMP bind {self.bind_ip}:{self.port} failed: {exc}")
            return
        self.log(f"SNMP listening on {self.bind_ip}:{self.port} sysDescr={self.sys_descr!r}")
        while not self._halt.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            response = build_get_response(data, {}, self.sys_descr, self.sys_name)
            if response:
                try:
                    sock.sendto(response, addr)
                    self.log(f"SNMP response -> {addr[0]}:{addr[1]} ({len(data)} in / {len(response)} out)")
                except OSError as exc:
                    self.log(f"SNMP send failed: {exc}")
