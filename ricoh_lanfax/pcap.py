"""Write a Wireshark-readable PCAP for a captured TCP 9100 session."""

from __future__ import annotations

import struct
import time
from ipaddress import ip_address

PCAP_MAGIC = 0xA1B2C3D4
LINKTYPE_ETHERNET = 1
ETH_IPV4 = 0x0800
TCP_9100 = 9100


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _ipv4(src: str, dst: str) -> tuple[bytes, bytes]:
    return ip_address(src).packed, ip_address(dst).packed


def _ethernet_frame(payload_ip: bytes, src_mac: bytes, dst_mac: bytes) -> bytes:
    return dst_mac + src_mac + struct.pack("!H", ETH_IPV4) + payload_ip


def _ipv4_tcp_segment(
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int,
    seq: int,
    ack: int,
    flags: int,
    payload: bytes,
    ident: int = 0x4000,
) -> bytes:
    src, dst = _ipv4(src_ip, dst_ip)
    tcp_len = 20 + len(payload)
    tcp_header_wo_sum = struct.pack(
        "!HHIIHHHH",
        src_port,
        dst_port,
        seq,
        ack,
        (5 << 12) | flags,  # data offset + flags
        64240,  # window
        0,
        0,
    )
    pseudo = src + dst + struct.pack("!BBH", 0, 6, tcp_len)
    csum = _checksum(pseudo + tcp_header_wo_sum + payload)
    tcp_header = tcp_header_wo_sum[:16] + struct.pack("!H", csum) + tcp_header_wo_sum[18:]
    tcp = tcp_header + payload

    total_len = 20 + len(tcp)
    ip_wo_sum = struct.pack("!BBHHHBBH", 0x45, 0, total_len, ident, 0x4000, 64, 6, 0) + src + dst
    ip_sum = _checksum(ip_wo_sum)
    ip_header = ip_wo_sum[:10] + struct.pack("!H", ip_sum) + ip_wo_sum[12:]
    return ip_header + tcp


def write_tcp_pcap(
    path: str,
    payload: bytes,
    src_ip: str,
    dst_ip: str,
    src_port: int,
    dst_port: int = TCP_9100,
    mss: int = 1400,
) -> None:
    """Write SYN/data/FIN as Ethernet+IPv4+TCP so Wireshark follows the stream."""
    now = time.time()
    ts_sec = int(now)
    ts_usec = int((now - ts_sec) * 1_000_000)
    src_mac = bytes.fromhex("020000000001")
    dst_mac = bytes.fromhex("020000000002")

    packets: list[bytes] = []
    seq_c, seq_s = 1, 1

    def add(ip_payload: bytes) -> None:
        packets.append(_ethernet_frame(ip_payload, src_mac, dst_mac))

    # Handshake
    add(_ipv4_tcp_segment(src_ip, dst_ip, src_port, dst_port, seq_c, 0, 0x02, b""))  # SYN
    seq_c += 1
    add(_ipv4_tcp_segment(dst_ip, src_ip, dst_port, src_port, seq_s, seq_c, 0x12, b""))  # SYN+ACK
    seq_s += 1
    add(_ipv4_tcp_segment(src_ip, dst_ip, src_port, dst_port, seq_c, seq_s, 0x10, b""))  # ACK

    offset = 0
    ident = 0x4001
    while offset < len(payload):
        chunk = payload[offset : offset + mss]
        add(
            _ipv4_tcp_segment(
                src_ip,
                dst_ip,
                src_port,
                dst_port,
                seq_c,
                seq_s,
                0x18,  # PSH+ACK
                chunk,
                ident=ident,
            )
        )
        seq_c += len(chunk)
        offset += len(chunk)
        ident += 1

    add(_ipv4_tcp_segment(src_ip, dst_ip, src_port, dst_port, seq_c, seq_s, 0x11, b""))  # FIN+ACK
    seq_c += 1
    add(_ipv4_tcp_segment(dst_ip, src_ip, dst_port, src_port, seq_s, seq_c, 0x11, b""))
    seq_s += 1

    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHIIII", PCAP_MAGIC, 2, 4, 0, 0, 65535, LINKTYPE_ETHERNET))
        for pkt in packets:
            fh.write(struct.pack("<IIII", ts_sec, ts_usec, len(pkt), len(pkt)))
            fh.write(pkt)
            ts_usec += 1000
            if ts_usec >= 1_000_000:
                ts_sec += 1
                ts_usec -= 1_000_000
