"""CUPS backend: save the job, open the Fax-Popup on the user display."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from .session import apply_session_env, drop_privs
from .spool import (
    append_log,
    job_paths,
    purge_stale_jobs,
    restrict_job_files,
    unlink_job_files,
    write_json,
)

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

    if os.geteuid() == 0:
        purge_stale_jobs()

    data = _read_job_bytes(argv)
    _err(f"INFO: job={job_id} bytes={len(data)}")
    if not data:
        _err(f"ERROR: job={job_id} status=empty")
        return 1

    from .send import is_cups_banner, materialize_document

    if is_cups_banner(data):
        data = materialize_document(data)
        _err(f"INFO: job={job_id} bytes={len(data)} (test page)")

    paths = job_paths(job_id)
    paths["doc"].write_bytes(data)
    write_json(
        paths["json"],
        {
            "job_id": job_id,
            "user": user,
            "title": argv[3] or "Dokument",
            "document": str(paths["doc"]),
            "copies": argv[4] if len(argv) > 4 else "1",
            "bytes": len(data),
            "ts": time.time(),
        },
    )
    try:
        restrict_job_files(user, paths["doc"], paths["json"])
    except Exception as exc:  # noqa: BLE001
        _err(f"ERROR: job={job_id} status=abort {exc}")
        unlink_job_files(paths)
        return 1

    try:
        drop_privs(user)
    except Exception as exc:  # noqa: BLE001
        _err(f"INFO: drop_privs: {exc}")
    env = apply_session_env(user)
    _err(f"INFO: uid={os.geteuid()} DISPLAY={env.get('DISPLAY')!r}")

    if paths["result"].exists():
        try:
            paths["result"].unlink()
        except OSError:
            pass

    from .popup import run_fax_popup

    _err(f"INFO: job={job_id} popup")
    try:
        try:
            status = run_fax_popup(paths["json"])
        except Exception as exc:  # noqa: BLE001
            _err(f"ERROR: job={job_id} status=popup {exc}")
            append_log(traceback.format_exc())
            return 1

        _err(f"INFO: job={job_id} status={status} bytes={len(data)}")
        if status == "ok":
            return 0
        if status == "cancel":
            return 5
        return 1
    finally:
        unlink_job_files(paths)


if __name__ == "__main__":
    raise SystemExit(run_backend(sys.argv))
