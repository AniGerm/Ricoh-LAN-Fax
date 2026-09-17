"""TCP 9100 (and optional IPP 631) capture sink that pretends to accept a print/fax job."""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .pcap import write_tcp_pcap
from .pjl import format_report, parse_job

DEFAULT_IDLE = 8.0
DEFAULT_FIRST_BYTE = 120.0


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def recv_until_idle(
    conn: socket.socket,
    first_byte_timeout: float,
    idle_timeout: float,
    max_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    conn.settimeout(first_byte_timeout)
    chunks: list[bytes] = []
    total = 0
    got_any = False
    while total < max_bytes:
        try:
            data = conn.recv(64 * 1024)
        except socket.timeout:
            if got_any:
                break
            raise
        if not data:
            break
        chunks.append(data)
        total += len(data)
        got_any = True
        conn.settimeout(idle_timeout)
    return b"".join(chunks)


def save_job(
    payload: bytes,
    out_dir: Path,
    src_ip: str,
    src_port: int,
    dst_ip: str,
    dst_port: int,
    log: Callable[[str], None],
    prefix: str = "job",
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    base = out_dir / f"{prefix}-{stamp}-{src_ip.replace(':', '_')}-{src_port}"
    raw_path = base.with_suffix(".raw")
    txt_path = base.with_suffix(".pjl.txt")
    pcap_path = base.with_suffix(".pcap")
    raw_path.write_bytes(payload)
    report = format_report(parse_job(payload))
    txt_path.write_text(report, encoding="utf-8")
    try:
        write_tcp_pcap(str(pcap_path), payload, src_ip, dst_ip, src_port, dst_port)
    except Exception as exc:  # noqa: BLE001 — capture must not die on pcap
        log(f"PCAP write failed: {exc}")
        pcap_path = Path()
    log(f"saved {raw_path.name} ({len(payload)} bytes)")
    log(f"  PJL report: {txt_path.name}")
    if pcap_path:
        log(f"  Wireshark: {pcap_path.name}  (display filter: tcp.port == {dst_port})")
    for line in report.strip().splitlines()[:20]:
        log(f"  {line}")
    return raw_path


class JobServer(threading.Thread):
    def __init__(
        self,
        bind_ip: str,
        port: int,
        out_dir: Path,
        log: Callable[[str], None],
        idle_timeout: float = DEFAULT_IDLE,
        label: str = "9100",
        ipp_http: bool = False,
    ) -> None:
        super().__init__(daemon=True, name=f"sink-{port}")
        self.bind_ip = bind_ip
        self.port = port
        self.out_dir = out_dir
        self.log = log
        self.idle_timeout = idle_timeout
        self.label = label
        self.ipp_http = ipp_http
        self._halt = threading.Event()
        self.ready = threading.Event()
        self.sock: socket.socket | None = None
        self.bound_port: int | None = None

    def stop(self) -> None:
        self._halt.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock = sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.bind_ip, self.port))
            sock.listen(8)
            sock.settimeout(0.5)
            self.bound_port = sock.getsockname()[1]
            self.ready.set()
        except OSError as exc:
            self.log(f"{self.label} bind {self.bind_ip}:{self.port} failed: {exc}")
            self.ready.set()
            return
        self.log(f"{self.label} listening on {self.bind_ip}:{self.bound_port}")
        while not self._halt.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle,
                args=(conn, addr),
                daemon=True,
                name=f"job-{addr[0]}:{addr[1]}",
            ).start()

    def _handle(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        peer_ip, peer_port = addr
        self.log(f"{self.label} connection from {peer_ip}:{peer_port}")
        try:
            if self.ipp_http:
                payload = self._read_http_or_raw(conn)
            else:
                payload = recv_until_idle(conn, DEFAULT_FIRST_BYTE, self.idle_timeout)
            if not payload:
                self.log(f"{self.label} empty job from {peer_ip}:{peer_port}")
                return
            save_job(
                payload,
                self.out_dir,
                peer_ip,
                peer_port,
                self.bind_ip if self.bind_ip != "0.0.0.0" else "127.0.0.1",
                self.bound_port or self.port,
                self.log,
                prefix=f"{self.label}",
            )
            if self.ipp_http and payload.startswith((b"POST", b"GET", b"PUT", b"HEAD")):
                try:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/ipp\r\n"
                        b"Content-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                except OSError:
                    pass
        except socket.timeout:
            self.log(f"{self.label} timeout waiting for data from {peer_ip}:{peer_port}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"{self.label} error from {peer_ip}:{peer_port}: {exc}")
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def _read_http_or_raw(self, conn: socket.socket) -> bytes:
        conn.settimeout(DEFAULT_FIRST_BYTE)
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 1024 * 1024:
            chunk = conn.recv(4096)
            if not chunk:
                return buf
            buf += chunk
            if not buf.startswith((b"POST", b"GET", b"PUT", b"HEAD", b"HTTP")):
                # Not HTTP — treat remainder as RAW (IPP-over-9100 is unusual).
                rest = recv_until_idle(conn, 0.2, self.idle_timeout)
                return buf + rest
        header, _, rest = buf.partition(b"\r\n\r\n")
        length = 0
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    length = 0
        body = rest
        conn.settimeout(self.idle_timeout)
        while len(body) < length:
            chunk = conn.recv(min(65536, length - len(body)))
            if not chunk:
                break
            body += chunk
        if length:
            return header + b"\r\n\r\n" + body
        extra = recv_until_idle(conn, 0.2, self.idle_timeout)
        return header + b"\r\n\r\n" + body + extra


def start_servers(
    bind_ip: str,
    port: int,
    out_dir: Path,
    log: Callable[[str], None],
    idle_timeout: float = DEFAULT_IDLE,
    enable_ipp: bool = False,
    ipp_port: int = 631,
    enable_snmp: bool = False,
    snmp_port: int = 161,
    enable_mdns: bool = False,
    sys_descr: str = "RICOH IM 350F",
    sys_name: str = "LAN-Fax-Sink",
) -> list[threading.Thread]:
    from .mdns import MdnsAnnouncer
    from .snmp import SnmpSink

    servers: list[threading.Thread] = []
    raw = JobServer(bind_ip, port, out_dir, log, idle_timeout, label="raw9100")
    raw.start()
    servers.append(raw)
    if enable_ipp:
        ipp = JobServer(
            bind_ip,
            ipp_port,
            out_dir,
            log,
            idle_timeout,
            label="ipp",
            ipp_http=True,
        )
        ipp.start()
        servers.append(ipp)
    if enable_snmp:
        snmp_bind = "0.0.0.0" if bind_ip == "0.0.0.0" else bind_ip
        snmp = SnmpSink(snmp_bind, snmp_port, sys_descr, sys_name, log)
        snmp.start()
        servers.append(snmp)
    if enable_mdns:
        mdns_ip = bind_ip
        if mdns_ip in ("0.0.0.0", ""):
            mdns_ip = guess_ipv4()
            log(f"mDNS using guessed IPv4 {mdns_ip} (pass --listen with the VM-facing address)")
        mdns = MdnsAnnouncer(mdns_ip, port, "RICOH IM 350F Sink", "im350f-sink", log)
        mdns.start()
        servers.append(mdns)
    log("Sink bereit — Windows LAN-Fax Port = diese IP, Raw 9100")
    return servers


def stop_servers(servers: list[threading.Thread], log: Callable[[str], None] | None = None) -> None:
    if log:
        log("stopping…")
    for srv in servers:
        stop = getattr(srv, "stop", None)
        if callable(stop):
            stop()


def run_sink(
    bind_ip: str,
    port: int,
    out_dir: Path,
    log: Callable[[str], None],
    idle_timeout: float = DEFAULT_IDLE,
    enable_ipp: bool = False,
    ipp_port: int = 631,
    enable_snmp: bool = False,
    snmp_port: int = 161,
    enable_mdns: bool = False,
    sys_descr: str = "RICOH IM 350F",
    sys_name: str = "LAN-Fax-Sink",
) -> None:
    servers = start_servers(
        bind_ip,
        port,
        out_dir,
        log,
        idle_timeout=idle_timeout,
        enable_ipp=enable_ipp,
        ipp_port=ipp_port,
        enable_snmp=enable_snmp,
        snmp_port=snmp_port,
        enable_mdns=enable_mdns,
        sys_descr=sys_descr,
        sys_name=sys_name,
    )
    log("Ctrl+C beendet den Sink")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop_servers(servers, log)


def guess_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
