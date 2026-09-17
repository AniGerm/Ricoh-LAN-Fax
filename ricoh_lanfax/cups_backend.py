"""CUPS backend: save the job, open the Fax-Popup on the user display."""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from .session import apply_session_env, drop_privs
from .spool import append_log, job_paths, write_json

DEVICE_LINE = 'direct ricohlanfax:/ "Ricoh LAN-Fax" "Ricoh LAN-Fax (number popup)"\n'


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
    append_log(msg)


def run_backend(argv: list[str]) -> int:
    try:
        return _run(argv)
    except Exception as exc:  # noqa: BLE001
        _err("ERROR: " + str(exc))
        append_log(traceback.format_exc())
        return 1


def _read_job_bytes(argv: list[str]) -> bytes:
    infile = argv[6] if len(argv) >= 7 and argv[6] else None
    if infile:
        try:
            data = Path(infile).read_bytes()
            if data:
                return data
        except OSError as exc:
            _err(f"INFO: Datei {infile} nicht lesbar: {exc}")
    return sys.stdin.buffer.read()


def _run(argv: list[str]) -> int:
    if len(argv) <= 1:
        sys.stdout.write(DEVICE_LINE)
        return 0
    if len(argv) < 6:
        _err(f"ERROR: ricohlanfax backend arguments: {argv!r}")
        return 1

    job_id, user, title = argv[1], argv[2], argv[3]
    _err(f"INFO: job={job_id} user={user} title={title!r} argc={len(argv)}")

    data = _read_job_bytes(argv)
    _err(f"INFO: gelesen {len(data)} bytes")
    if not data:
        _err("ERROR: leerer Druckauftrag (kein PDF auf stdin)")
        return 1

    from .send import is_cups_banner, materialize_document

    if is_cups_banner(data):
        data = materialize_document(data)
        _err(f"INFO: CUPS-Testseite -> {len(data)} bytes PDF/PS")

    paths = job_paths(job_id)
    paths["doc"].write_bytes(data)
    write_json(
        paths["json"],
        {
            "job_id": job_id,
            "user": user,
            "title": title or "Dokument",
            "document": str(paths["doc"]),
            "copies": argv[4] if len(argv) > 4 else "1",
            "bytes": len(data),
            "ts": time.time(),
        },
    )
    for p in (paths["doc"], paths["json"]):
        try:
            p.chmod(0o666)
        except OSError:
            pass
    _err(f"INFO: Spool {paths['json']}")

    try:
        drop_privs(user)
    except Exception as exc:  # noqa: BLE001
        _err(f"INFO: drop_privs: {exc}")
    env = apply_session_env(user)
    _err(f"INFO: uid={__import__('os').geteuid()} DISPLAY={env.get('DISPLAY')!r}")

    if paths["result"].exists():
        try:
            paths["result"].unlink()
        except OSError:
            pass

    from .popup import run_fax_popup

    _err("INFO: öffne Drucker-Popup")
    try:
        status = run_fax_popup(paths["json"])
    except Exception as exc:  # noqa: BLE001
        _err(f"ERROR: Popup: {exc}")
        append_log(traceback.format_exc())
        return 1

    _err(f"INFO: Popup-Ergebnis {status}")
    if status == "ok":
        return 0
    if status == "cancel":
        return 5
    return 1


if __name__ == "__main__":
    raise SystemExit(run_backend(sys.argv))
