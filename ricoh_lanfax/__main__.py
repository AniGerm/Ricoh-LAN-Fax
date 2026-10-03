"""CLI: sink, parse, diff, send, expand-strings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _log(message: str) -> None:
    print(message, flush=True)


def _parse_hostport(value: str, default_port: int) -> tuple[str, int]:
    if ":" in value:
        host, port_s = value.rsplit(":", 1)
        return host or "0.0.0.0", int(port_s)
    return value, default_port


def cmd_sink(args: argparse.Namespace) -> int:
    from .sink import run_sink

    host, port = _parse_hostport(args.listen, 9100)
    out = Path(args.out)
    run_sink(
        bind_ip=host,
        port=port,
        out_dir=out,
        log=_log,
        idle_timeout=args.idle,
        enable_ipp=args.ipp,
        ipp_port=args.ipp_port,
        enable_snmp=args.snmp,
        snmp_port=args.snmp_port,
        enable_mdns=args.mdns,
        sys_descr=args.sys_descr,
        sys_name=args.sys_name,
    )
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    from .pjl import format_report, parse_job

    data = Path(args.file).read_bytes()
    sys.stdout.write(format_report(parse_job(data)))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from .pjl import diff_jobs

    a = Path(args.file_a).read_bytes()
    b = Path(args.file_b).read_bytes()
    sys.stdout.write(diff_jobs(a, b, label_a=args.file_a, label_b=args.file_b))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    from .send import build_job, send_raw

    source = Path(args.file) if args.file else None
    template = Path(args.template) if args.template else None
    if template is None and source is not None and source.suffix.lower() == ".raw":
        template = source
        source = None
    if template is None and source is None:
        _log("send: pass a PDF/TIFF file or --template captures/job.raw")
        return 2
    job, note = build_job(
        source=source,
        number=args.number,
        template=template,
        replace_from=args.replace_number,
        resolution=args.resolution,
    )
    _log(note)
    if args.dump:
        dump = Path(args.dump)
        dump.write_bytes(job)
        _log(f"wrote {dump} ({len(job)} bytes)")
    if not args.dry_run:
        send_raw(args.host, args.port, job)
        _log(f"sent {len(job)} bytes to {args.host}:{args.port}")
    else:
        _log("dry-run: nothing sent")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    from .catalog import build_catalog

    text = build_catalog(Path(args.expanded))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    _log(f"wrote {out}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    from .expand_strings import expand_and_scan

    disk1 = Path(args.disk1)
    out = Path(args.out)
    sys.stdout.write(expand_and_scan(disk1, out))
    return 0


def cmd_popup(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .popup import run_fax_popup

    status = run_fax_popup(Path(args.job_json))
    _log(f"popup: {status}")
    return 0 if status == "ok" else 1


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui()


def cmd_settings(_args: argparse.Namespace) -> int:
    from .gui import run_settings_app

    return run_settings_app()


def cmd_cups(args: argparse.Namespace) -> int:
    from .cups_backend import run_backend

    return run_backend(["ricohlanfax", *args.cups_args])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ricoh-lanfax",
        description="Ricoh LAN-Fax for Linux: CUPS popup sender and optional lab tools.",
    )
    parser.add_argument("--version", action="version", version=f"ricoh-lanfax {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sink = sub.add_parser("sink", help="Fake printer: accept TCP 9100 jobs and save RAW+PCAP")
    sink.add_argument("--listen", default="0.0.0.0:9100", help="Bind host:port for RAW (default 0.0.0.0:9100)")
    sink.add_argument("--out", default="captures", help="Output directory")
    sink.add_argument("--idle", type=float, default=8.0, help="Seconds of silence after last byte = job end")
    sink.add_argument("--ipp", action="store_true", help="Also listen for IPP HTTP on --ipp-port")
    sink.add_argument("--ipp-port", type=int, default=631)
    sink.add_argument("--snmp", action="store_true", help="Minimal SNMPv1 sysDescr (needs cap_net_bind if port 161)")
    sink.add_argument("--snmp-port", type=int, default=161)
    sink.add_argument("--mdns", action="store_true", help="Announce _pdl-datastream._tcp on the bind address")
    sink.add_argument("--sys-descr", default="RICOH IM 350F")
    sink.add_argument("--sys-name", default="LAN-Fax-Sink")
    sink.set_defaults(func=cmd_sink)

    parse = sub.add_parser("parse", help="Pretty-print UEL/PJL vs binary payload")
    parse.add_argument("file")
    parse.set_defaults(func=cmd_parse)

    diff = sub.add_parser("diff", help="Compare two captured jobs (find the fax number bytes)")
    diff.add_argument("file_a")
    diff.add_argument("file_b")
    diff.set_defaults(func=cmd_diff)

    send = sub.add_parser("send", help="Send a PDF/TIFF as RAW PJL/RFAX, or replay a capture template")
    send.add_argument("file", nargs="?", help="PDF, PS, or TIFF (ignored if --template)")
    send.add_argument("--host", required=True, help="Printer or sink IPv4")
    send.add_argument("--port", type=int, default=9100)
    send.add_argument("--number", required=True, help="Destination fax number")
    send.add_argument("--template", help="Captured .raw from the sink; preferred over guessed wrapper")
    send.add_argument("--replace-number", help="ASCII token in the template to replace (default: first phone-like)")
    send.add_argument("--resolution", default="200", help="Ghostscript -r (Windows capture is 200 dpi)")
    send.add_argument("--dump", help="Write the built job to this path")
    send.add_argument("--dry-run", action="store_true")
    send.set_defaults(func=cmd_send)

    expand = sub.add_parser("expand-strings", help="Unpack DISK1 .dl_ files and list PJL/RFAX strings")
    expand.add_argument("disk1", nargs="?", default="DISK1")
    expand.add_argument("--out", default="captures/expanded")
    expand.set_defaults(func=cmd_expand)

    catalog = sub.add_parser("catalog", help="Readable PJL/RFAX map from unpacked driver files")
    catalog.add_argument("--expanded", default="captures/expanded")
    catalog.add_argument("--out", default="captures/pjl-rfax.txt")
    catalog.set_defaults(func=cmd_catalog)

    gui = sub.add_parser("gui", help="Labor-GUI (Capture-Sink, nicht in der .deb-Installation)")
    gui.set_defaults(func=cmd_gui)

    settings = sub.add_parser("settings", help="Ubuntu-Menü: Drucker-IP/Port und Telefonbuch")
    settings.set_defaults(func=cmd_settings)

    popup = sub.add_parser("popup", help="Fax-Nummern-Dialog für einen Spool-Job")
    popup.add_argument("job_json")
    popup.set_defaults(func=cmd_popup)

    cups = sub.add_parser("cups-backend", help="CUPS backend (von install-printer.sh)")
    cups.add_argument("cups_args", nargs="*", help=argparse.SUPPRESS)
    cups.set_defaults(func=cmd_cups)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "cups-backend":
        from .cups_backend import run_backend

        return run_backend(["ricohlanfax", *raw[1:]])
    parser = build_parser()
    args = parser.parse_args(raw)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
