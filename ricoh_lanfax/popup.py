"""Standalone Fax-Popup — vom CUPS-Backend gestartet, keine Hintergrund-App."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .spool import write_result


def run_fax_popup(job_json: Path) -> str:
    """Show the number dialog. Returns ok / cancel / error."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    from .gui import FaxPopup
    from .theme import apply_theme

    data: dict[str, Any] = json.loads(job_json.read_text(encoding="utf-8"))
    jid = str(data.get("job_id") or job_json.stem)
    doc = Path(str(data.get("document") or ""))
    title = str(data.get("title") or "Fax")
    project = Path.home() / ".local/share" / "ricoh-lanfax"
    project.mkdir(parents=True, exist_ok=True)
    (project / "captures").mkdir(exist_ok=True)

    result = {"status": "cancel", "message": ""}

    root = tk.Tk()
    root.title("Ricoh-LAN-Fax")
    apply_theme(root)

    def done(status: str, message: str) -> None:
        result["status"] = status
        result["message"] = message
        try:
            write_result(jid, status, message)
        except OSError:
            pass
        root.quit()

    popup = FaxPopup(
        tk,
        ttk,
        messagebox,
        root,
        project,
        data,
        doc if doc.exists() else None,
        title,
        done,
        origin="drucker",
        as_root=True,
    )
    popup.win.lift()
    try:
        popup.win.attributes("-topmost", True)
    except tk.TclError:
        pass
    popup.win.focus_force()

    result_path = job_json.with_suffix(".result")

    def poll_result() -> None:
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
                result["status"] = str(data.get("status") or result["status"])
                result["message"] = str(data.get("message") or "")
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            root.quit()
            return
        root.after(400, poll_result)

    root.after(400, poll_result)
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
    return str(result["status"])
