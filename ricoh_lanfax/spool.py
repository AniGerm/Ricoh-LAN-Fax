"""Print-job spool between the CUPS backend and the desktop popup."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SHARED_SPOOL = Path("/var/tmp/ricoh-lanfax/spool")
SHARED_LOG = Path("/var/tmp/ricoh-lanfax/backend.log")


def spool_dir(home: Path | None = None) -> Path:
    _ = home
    if os.environ.get("RICOH_LANFAX_SPOOL"):
        path = Path(os.environ["RICOH_LANFAX_SPOOL"])
    else:
        path = SHARED_SPOOL
    try:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o1777)
        except OSError:
            pass
    except OSError:
        path = Path("/tmp/ricoh-lanfax-spool")
        path.mkdir(parents=True, exist_ok=True)
    return path


def job_paths(job_id: str, home: Path | None = None) -> dict[str, Path]:
    root = spool_dir(home)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in job_id) or "job"
    return {
        "json": root / f"job-{safe}.json",
        "doc": root / f"job-{safe}.pdf",
        "result": root / f"job-{safe}.result",
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pending_jobs(home: Path | None = None) -> list[dict[str, Any]]:
    root = spool_dir(home)
    jobs: list[dict[str, Any]] = []
    for meta in sorted(root.glob("job-*.json")):
        result = meta.with_suffix(".result")
        if result.exists():
            continue
        try:
            data = read_json(meta)
        except (OSError, json.JSONDecodeError):
            continue
        data["_meta"] = str(meta)
        jobs.append(data)
    return jobs


def write_result(job_id: str, status: str, message: str = "", home: Path | None = None) -> None:
    paths = job_paths(job_id, home)
    write_json(paths["result"], {"status": status, "message": message, "ts": time.time()})


def append_log(message: str) -> None:
    try:
        SHARED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SHARED_LOG.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message.rstrip() + "\n")
    except OSError:
        pass
