"""Minimal mDNS/DNS-SD announce for a RAW printer on port 9100 (VM-facing only)."""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _ptr(name: str, target: str, ttl: int = 120) -> bytes:
    rdata = _encode_name(target)
    return _encode_name(name) + struct.pack("!HHIH", 12, 1, ttl, len(rdata)) + rdata


def _txt(name: str, pairs: dict[str, str], ttl: int = 120) -> bytes:
    rdata = b""
    for key, value in pairs.items():
        item = f"{key}={value}".encode("utf-8")
        rdata += bytes([len(item)]) + item
    return _encode_name(name) + struct.pack("!HHIH", 16, 1, ttl, len(rdata)) + rdata


def _srv(name: str, host: str, port: int, ttl: int = 120) -> bytes:
    rdata = struct.pack("!HHH", 0, 0, port) + _encode_name(host)
    return _encode_name(name) + struct.pack("!HHIH", 33, 1, ttl, len(rdata)) + rdata


def _a(name: str, ipv4: str, ttl: int = 120) -> bytes:
    parts = [int(p) for p in ipv4.split(".")]
    rdata = bytes(parts)
    return _encode_name(name) + struct.pack("!HHIH", 1, 1, ttl, len(rdata)) + rdata


def build_announce(instance: str, hostname: str, ipv4: str, port: int) -> bytes:
    service = "_pdl-datastream._tcp.local"
    inst = f"{instance}.{service}"
    host = f"{hostname}.local"
    answers = [
        _ptr(service, inst),
        _srv(inst, host, port),
        _txt(
            inst,
            {
                "txtvers": "1",
                "ty": "RICOH IM 350F",
                "product": "(RICOH IM 350F)",
                "usb_MFG": "RICOH",
                "usb_MDL": "IM 350F",
                "pdl": "application/vnd.hp-pjl,application/octet-stream",
                "note": "LAN-Fax capture sink",
            },
        ),
        _a(host, ipv4),
    ]
    body = b"".join(answers)
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, len(answers), 0, 0)
    return header + body


class MdnsAnnouncer(threading.Thread):
    def __init__(
        self,
        bind_ip: str,
        port: int,
        instance: str,
        hostname: str,
        log: Callable[[str], None],
        interval: float = 15.0,
    ) -> None:
        super().__init__(daemon=True, name="mdns-announce")
        self.bind_ip = bind_ip
        self.port = port
        self.instance = instance
        self.hostname = hostname
        self.log = log
        self.interval = interval
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
        packet = build_announce(self.instance, self.hostname, self.bind_ip, self.port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.bind_ip))
            sock.bind((self.bind_ip, 0))
        except OSError as exc:
            self.log(f"mDNS setup failed: {exc}")
            return
        self.log(f"mDNS announcing {self.instance!r} on {self.bind_ip} (group {MDNS_GROUP}:{MDNS_PORT})")
        while not self._halt.is_set():
            try:
                sock.sendto(packet, (MDNS_GROUP, MDNS_PORT))
            except OSError as exc:
                self.log(f"mDNS send failed: {exc}")
                return
            self._halt.wait(self.interval)
