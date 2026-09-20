"""Desktop GUI: Ubuntu-Drucker-Popup, Ricoh-IP, optional Capture-Sink."""

from __future__ import annotations

import math
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from .config import Settings, load_settings, parse_numbers, save_settings
from .phonebook import Contact, Recent, compact_number, load_book, save_book
from .theme import apply_theme, button, prepare_treeview, style_text
from .pjl import format_report, parse_job
from .sink import guess_ipv4, start_servers, stop_servers
from .spool import pending_jobs

ZOOM_STEPS = (50, 75, 100, 125, 150, 200, 250, 300)


def _parse_zoom(value: str) -> int | None:
    try:
        pct = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if pct in ZOOM_STEPS:
        return pct
    return min(ZOOM_STEPS, key=lambda s: abs(s - pct))


def _scale_photo(img: Any, percent: int) -> Any:
    if percent == 100:
        return img
    num, den = percent, 100
    g = math.gcd(num, den)
    num, den = num // g, den // g
    if percent < 100:
        if den != 1:
            img = img.subsample(den, den)
        if num != 1:
            img = img.zoom(num, num)
    else:
        if num != 1:
            img = img.zoom(num, num)
        if den != 1:
            img = img.subsample(den, den)
    return img


def _tk_fit_photo(img: Any, max_w: int, max_h: int) -> Any:
    """Integer zoom/subsample that fills the window as far as possible."""
    iw, ih = img.width(), img.height()
    if iw < 1 or ih < 1:
        return img
    max_w, max_h = max(32, max_w), max(32, max_h)
    grow = min(max_w // iw, max_h // ih)
    if grow >= 2:
        return img.zoom(grow, grow)
    factor = 1
    while factor < 8 and (iw // factor > max_w or ih // factor > max_h):
        factor += 1
    if factor <= 1:
        return img
    return img.subsample(factor, factor)


def _resize_preview(path: Path, master: Any, box: tuple[int, int] | None, percent: int) -> Any | None:
    """Smooth resize via Pillow (avoids black boxes from Tk subsample)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(path)
        im.load()
    except OSError:
        return None
    if im.mode == "RGBA":
        bg = Image.new("L", im.size, 255)
        bg.paste(im.convert("L"), mask=im.split()[-1])
        im = bg
    elif im.mode not in ("L", "1"):
        im = im.convert("L")
    w, h = im.size
    if box is not None:
        tw, th = max(32, box[0]), max(32, box[1])
        scale = min(tw / max(w, 1), th / max(h, 1))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    else:
        nw, nh = max(1, w * percent // 100), max(1, h * percent // 100)
    if (nw, nh) != (w, h):
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        im = im.resize((nw, nh), resample)
    try:
        from PIL import ImageTk

        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        pass
    import tkinter as tk

    tmp = path.with_name(f".scaled-{path.stem}-{nw}x{nh}.png")
    try:
        im.save(tmp)
        name = f"faxfit-{path.stem}-{nw}x{nh}-{id(im)}"
        return tk.PhotoImage(master=master, file=str(tmp), name=name)
    except (OSError, tk.TclError):
        return None


def list_ipv4() -> list[str]:
    addrs: list[str] = []
    try:
        import socket as sockmod

        hostname = sockmod.gethostname()
        for info in sockmod.getaddrinfo(hostname, None, sockmod.AF_INET):
            ip = info[4][0]
            if ip not in addrs and not ip.startswith("127."):
                addrs.append(ip)
    except OSError:
        pass
    guessed = guess_ipv4()
    if guessed not in addrs:
        addrs.insert(0, guessed)
    return addrs or ["127.0.0.1"]


def latest_raw(out_dir: Path) -> Path | None:
    raws = sorted(out_dir.glob("raw9100-*.raw"), key=lambda p: p.stat().st_mtime, reverse=True)
    return raws[0] if raws else None


class SettingsDialog:
    def __init__(self, tk: Any, ttk: Any, messagebox: Any, parent: Any, settings: Settings, on_save: Callable[[Settings], None]) -> None:
        self.messagebox = messagebox
        self.on_save = on_save
        win = tk.Toplevel(parent)
        self.win = win
        win.title("Ricoh-Drucker")
        win.resizable(False, False)
        win.transient(parent)
        apply_theme(win)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="IP der IM 350F (Raw 9100), nicht die Ubuntu-Adresse:").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text="IP").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.host = ttk.Entry(frm, width=22)
        self.host.grid(row=1, column=1, columnspan=2, sticky="we", pady=(8, 0), padx=(8, 0))
        self.host.insert(0, settings.printer_host)
        ttk.Label(frm, text="Port").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.port = ttk.Entry(frm, width=8)
        self.port.grid(row=2, column=1, sticky="w", pady=(8, 0), padx=(8, 0))
        self.port.insert(0, str(settings.printer_port))
        self.status = ttk.Label(frm, text="", style="Muted.TLabel")
        self.status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.save_dump = tk.BooleanVar(master=win, value=bool(settings.save_debug_dump))
        ttk.Checkbutton(
            frm,
            text="Debug-Dump der letzten Übertragung speichern (0600)",
            variable=self.save_dump,
            onvalue=True,
            offvalue=False,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky="e", pady=(12, 0))
        button(btns, text="Verbindung prüfen", command=self.test).pack(side="left", padx=(0, 8))
        button(btns, text="Abbrechen", command=win.destroy).pack(side="left", padx=4)
        button(btns, text="Speichern", command=self.save, variant="primary").pack(side="left", padx=4)
        win.grab_set()
        self.host.focus_set()

    def _read(self) -> Settings:
        host = self.host.get().strip()
        try:
            port = int(self.port.get().strip() or "9100")
        except ValueError as exc:
            raise ValueError("Port muss eine Zahl sein") from exc
        current = load_settings()
        current.printer_host = host
        current.printer_port = port
        current.save_debug_dump = bool(self.save_dump.get())
        return current

    def test(self) -> None:
        try:
            from .send import probe_printer

            s = self._read()
            msg = probe_printer(s.printer_host, s.printer_port)
            self.status.config(text=msg)
        except Exception as exc:  # noqa: BLE001
            self.status.config(text=str(exc))

    def save(self) -> None:
        try:
            settings = self._read()
        except ValueError as exc:
            self.messagebox.showerror("Einstellungen", str(exc), parent=self.win)
            return
        save_settings(settings)
        self.on_save(settings)
        self.win.destroy()


class PhonebookDialog:
    FILTERS = (("recent", "Zuletzt benutzt"), ("favorite", "Favoriten"), ("all", "Alle"))

    def __init__(
        self,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        parent: Any,
        on_pick: Callable[[list[str]], None],
    ) -> None:
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.on_pick = on_pick
        self.book = load_book()
        self._filter = "all"
        self._rows: dict[str, tuple[str, Contact | Recent]] = {}
        self._search_job = ""
        win = tk.Toplevel(parent)
        self.win = win
        win.title("Telefonbuch")
        win.geometry("780x520")
        win.minsize(640, 420)
        win.transient(parent)
        win.bind("<Escape>", lambda _e: win.destroy())
        apply_theme(win)

        root = ttk.Frame(win, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        search_row = ttk.Frame(root)
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(search_row, text="Suchen").pack(side="left")
        self.search = ttk.Entry(search_row)
        self.search.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.search.bind("<KeyRelease>", self._on_search)
        self.search.bind("<Return>", lambda _e: self.apply())

        side = ttk.Frame(root)
        side.grid(row=1, column=0, sticky="nsw", padx=(0, 10))
        self._filter_var = tk.StringVar(master=win, value="all")
        radios: list[Any] = []
        for key, label in self.FILTERS:
            btn = ttk.Radiobutton(side, text=label, value=key, variable=self._filter_var)
            btn.pack(anchor="w", pady=2)
            radios.append((key, btn))

        table = ttk.Frame(root)
        table.grid(row=1, column=1, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        cols = ("name", "number", "extra")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="extended")
        prepare_treeview(
            self.tree,
            [
                ("name", "Name", 260, True, "w"),
                ("number", "Nummer", 180, False, "w"),
                ("extra", "", 120, False, "w"),
            ],
        )
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self.apply())
        self.tree.bind("<Return>", lambda _e: self.apply())

        self.status = ttk.Label(root, text="", style="Muted.TLabel")
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(root)
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        button(btns, text="Neuer Eintrag", command=self.add_entry).pack(side="left")
        button(btns, text="Speichern…", command=self.save_selected).pack(side="left", padx=(8, 0))
        button(btns, text="Favorit", command=self.toggle_favorite).pack(side="left", padx=(8, 0))
        button(btns, text="Löschen", command=self.delete_selected).pack(side="left", padx=(8, 0))
        button(btns, text="Schließen", command=win.destroy).pack(side="right")
        button(btns, text="Übernehmen", command=self.apply, variant="primary").pack(side="right", padx=(0, 8))

        for key, btn in radios:
            btn.configure(command=lambda k=key: self._set_filter(k))
        self._set_filter("all")
        self.search.focus_set()

    def _query(self) -> str:
        return self.search.get().strip()

    def _on_search(self, _event: Any | None = None) -> None:
        if self._search_job:
            try:
                self.win.after_cancel(self._search_job)
            except Exception:  # noqa: BLE001
                pass
        self._search_job = self.win.after(80, self.refresh)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._filter_var.set(key)
        self.refresh()

    def _selected(self) -> list[tuple[str, Contact | Recent]]:
        out: list[tuple[str, Contact | Recent]] = []
        for iid in self.tree.selection():
            row = self._rows.get(str(iid))
            if row:
                out.append(row)
        return out

    def refresh(self) -> None:
        if not hasattr(self, "tree"):
            return
        self._search_job = ""
        query = self._query()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._rows.clear()
        rows: list[tuple[str, str, str, str]] = []
        if self._filter == "recent":
            for _score, recent, contact in self.book.search_recents(query):
                name = contact.name if contact else "—"
                extra = "gespeichert" if contact else "nicht gespeichert"
                if contact and contact.favorite:
                    extra = "★  " + extra
                iid = f"r-{recent.compact()}"
                rows.append((iid, name, recent.number, extra))
                self._rows[iid] = ("recent", recent)
        elif self._filter == "favorite":
            hits = self.book.search(query, favorite_only=True) if query else [(1.0, c) for c in self.book.favorites()]
            for _score, contact in hits:
                extra = "★" if contact.source == "local" else f"★  {contact.source}"
                iid = f"c-{contact.id}"
                rows.append((iid, contact.label(), contact.number, extra))
                self._rows[iid] = ("contact", contact)
        else:
            hits = self.book.search(query) if query else [(1.0, c) for c in self.book.named_sorted()]
            for _score, contact in hits:
                extra = "★" if contact.favorite else ("" if contact.source == "local" else contact.source)
                iid = f"c-{contact.id}"
                rows.append((iid, contact.label(), contact.number, extra))
                self._rows[iid] = ("contact", contact)
        for iid, name, number, extra in rows:
            self.tree.insert("", "end", iid=iid, values=(name, number, extra))
        hint = {
            "all": "Adressen nach Name",
            "recent": "Zuletzt verwendete Nummern",
            "favorite": "Favoriten",
        }[self._filter]
        notes = self.book.source_notes()
        extra = ("  ·  " + " ".join(notes)) if notes else ""
        self.status.config(text=f"{hint}  ·  {len(rows)} Einträge{extra}")

    def _ask_contact(
        self,
        *,
        title: str,
        number: str = "",
        name: str = "",
        favorite: bool = False,
    ) -> tuple[str, str, bool] | None:
        tk, ttk = self.tk, self.ttk
        dlg = tk.Toplevel(self.win)
        dlg.title(title)
        dlg.transient(self.win)
        dlg.resizable(False, False)
        apply_theme(dlg)
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Name").pack(anchor="w")
        name_entry = ttk.Entry(frm, width=36)
        name_entry.pack(fill="x", pady=(2, 0))
        name_entry.insert(0, name)
        ttk.Label(frm, text="Faxnummer").pack(anchor="w", pady=(8, 0))
        number_entry = ttk.Entry(frm, width=36)
        number_entry.pack(fill="x", pady=(2, 0))
        number_entry.insert(0, number)
        fav = tk.BooleanVar(master=dlg, value=favorite)
        ttk.Checkbutton(frm, text="Als Favorit", variable=fav).pack(anchor="w", pady=(8, 0))
        result: dict[str, Any] = {"ok": False}

        def ok() -> None:
            result["ok"] = True
            result["name"] = name_entry.get()
            result["number"] = number_entry.get()
            result["favorite"] = bool(fav.get())
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        button(btns, text="Abbrechen", command=dlg.destroy).pack(side="right")
        button(btns, text="Speichern", command=ok, variant="primary").pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", lambda _e: ok())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.grab_set()
        if name.strip():
            number_entry.focus_set()
            number_entry.icursor("end")
        else:
            name_entry.focus_set()
        dlg.wait_window()
        if not result.get("ok"):
            return None
        return str(result.get("name") or ""), str(result.get("number") or ""), bool(result.get("favorite"))

    def _commit_contact(self, number: str, name: str, favorite: bool) -> None:
        try:
            contact = self.book.save_named(number, name, favorite=favorite)
            save_book(self.book)
        except ValueError as exc:
            self.messagebox.showerror("Telefonbuch", str(exc), parent=self.win)
            return
        self.search.delete(0, "end")
        self._set_filter("all")
        iid = f"c-{contact.id}"
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def add_entry(self) -> None:
        asked = self._ask_contact(title="Neuer Eintrag")
        if asked is None:
            return
        name, number, favorite = asked
        self._commit_contact(number, name, favorite)

    def save_selected(self) -> None:
        rows = self._selected()
        if not rows:
            self.add_entry()
            return
        kind, item = rows[0]
        number = item.number
        name = ""
        favorite = False
        if kind == "contact" and isinstance(item, Contact):
            if item.source != "local":
                self.messagebox.showinfo("Telefonbuch", "LDAP/vCard-Einträge sind schreibgeschützt.", parent=self.win)
                return
            name = item.name
            favorite = item.favorite
        elif kind == "recent" and isinstance(item, Recent):
            linked = self.book.lookup_number(item.number)
            if linked is not None:
                name = linked.name
                favorite = linked.favorite
        asked = self._ask_contact(title="Eintrag speichern", number=number, name=name, favorite=favorite)
        if asked is None:
            return
        new_name, new_number, fav = asked
        self._commit_contact(new_number, new_name, fav)

    def toggle_favorite(self) -> None:
        rows = self._selected()
        if not rows:
            return
        kind, item = rows[0]
        contact: Contact | None = None
        if kind == "contact" and isinstance(item, Contact):
            contact = item
        elif kind == "recent" and isinstance(item, Recent):
            contact = self.book.lookup_number(item.number)
        if contact is None:
            self.messagebox.showinfo(
                "Telefonbuch",
                "Zuerst mit Namen speichern, dann als Favorit markieren.",
                parent=self.win,
            )
            return
        if contact.source != "local":
            self.messagebox.showinfo("Telefonbuch", "LDAP/vCard-Einträge sind schreibgeschützt.", parent=self.win)
            return
        self.book.set_favorite(contact.id, not contact.favorite)
        save_book(self.book)
        self.refresh()

    def delete_selected(self) -> None:
        rows = self._selected()
        if not rows:
            return
        kind, item = rows[0]
        if kind != "contact" or not isinstance(item, Contact) or item.source != "local":
            self.messagebox.showinfo(
                "Telefonbuch",
                "Nur lokal gespeicherte Adressen können gelöscht werden.",
                parent=self.win,
            )
            return
        if not self.messagebox.askyesno("Telefonbuch", f"„{item.label()}“ löschen?", parent=self.win):
            return
        self.book.delete_local(item.id)
        save_book(self.book)
        self.refresh()

    def apply(self) -> None:
        selected = self._selected()
        if not selected:
            children = self.tree.get_children()
            if children:
                self.tree.selection_set(children[0])
                selected = self._selected()
        numbers: list[str] = []
        for _kind, item in selected:
            num = compact_number(item.number)
            if num and num not in numbers:
                numbers.append(num)
        if not numbers:
            self.messagebox.showinfo("Telefonbuch", "Bitte einen Eintrag auswählen.", parent=self.win)
            return
        self.on_pick(numbers)
        self.win.destroy()


class FaxPopup:
    def __init__(
        self,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        parent: Any,
        project: Path,
        job: dict[str, Any] | None,
        document: Path | None,
        title: str,
        on_done: Callable[[str, str], None] | None,
        origin: str = "drucker",
        as_root: bool = False,
    ) -> None:
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.project = project
        self.document = document
        self.doc_title = title
        self.on_done = on_done
        self.origin = origin
        self.settings = load_settings()
        self._preview_files: list[Path] = []
        self._preview_index = 0
        self._photo = None
        self._photo_src = None
        self._photo_src_path: Path | None = None
        self._zoom: int | None = None if self.settings.preview_zoom in {"", "fit"} else _parse_zoom(self.settings.preview_zoom)
        self._last_canvas = (0, 0)
        self._preview_gen = 0
        self._recipients: list[tuple[str, str]] = []
        self._sending = False
        if as_root:
            win = parent
        else:
            win = tk.Toplevel(parent)
            win.transient(parent)
        self.win = win
        apply_theme(win)
        win.title(self._window_title())
        win.minsize(560, 720)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()
        win.focus_force()

        self.main = ttk.Frame(win)
        self.preview = ttk.Frame(win)
        self.main.pack(fill="both", expand=True)
        self._build_main()
        self._build_preview()
        self._size_to_main()
        win.bind("<Left>", lambda _e: self._turn_page(-1) if self.preview.winfo_ismapped() else None)
        win.bind("<Right>", lambda _e: self._turn_page(1) if self.preview.winfo_ismapped() else None)
        win.bind("<plus>", lambda _e: self._zoom_delta(1) if self.preview.winfo_ismapped() else None)
        win.bind("<minus>", lambda _e: self._zoom_delta(-1) if self.preview.winfo_ismapped() else None)
        win.bind("<KP_Add>", lambda _e: self._zoom_delta(1) if self.preview.winfo_ismapped() else None)
        win.bind("<KP_Subtract>", lambda _e: self._zoom_delta(-1) if self.preview.winfo_ismapped() else None)
        win.bind("0", lambda _e: self._set_zoom(None) if self.preview.winfo_ismapped() else None)
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        if not self.settings.printer_host.strip():
            win.after(200, self.open_settings)
        self.number_add.focus_set()

    def _build_main(self) -> None:
        tk, ttk = self.tk, self.ttk
        frm = self.main
        btns = ttk.Frame(frm)
        btns.pack(side="bottom", fill="x", padx=16, pady=(8, 16))
        button(btns, text="Abbrechen", command=self.cancel).pack(side="right")
        self.send_btn = button(btns, text="Senden", command=self.send, variant="primary")
        self.send_btn.pack(side="right", padx=8)
        button(btns, text="Vorschau", command=self.show_preview).pack(side="left")
        self.status = ttk.Label(frm, text="", style="Muted.TLabel")
        self.status.pack(side="bottom", fill="x", padx=16)

        top = ttk.Frame(frm)
        top.pack(fill="x", padx=16, pady=(14, 0))
        self.target_lbl = ttk.Label(top, text=self._target_text(), style="Title.TLabel")
        self.target_lbl.pack(side="left")
        ttk.Label(top, text=self._origin_text(), style="Muted.TLabel").pack(side="left", padx=(8, 0))
        button(top, text="⚙", command=self.open_settings, compact=True).pack(side="right")

        ttk.Label(frm, text=self.doc_title, style="Title.TLabel").pack(anchor="w", padx=16, pady=(10, 0))
        if self.document:
            ttk.Label(frm, text=self.document.name, style="Muted.TLabel").pack(anchor="w", padx=16)

        ttk.Label(frm, text="Empfänger").pack(anchor="w", padx=16, pady=(12, 0))
        table = ttk.Frame(frm)
        table.pack(fill="x", padx=16, pady=(6, 0))
        table.columnconfigure(0, weight=1)
        cols = ("number", "name", "drop")
        self.recipients = ttk.Treeview(table, columns=cols, show="headings", height=4, selectmode="browse")
        prepare_treeview(
            self.recipients,
            [
                ("number", "Nummer", 170, True, "w"),
                ("name", "Name", 220, True, "w"),
                ("drop", "✕", 48, False, "center"),
            ],
        )
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.recipients.yview)
        self.recipients.configure(yscrollcommand=yscroll.set)
        self.recipients.grid(row=0, column=0, sticky="ew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.recipients.bind("<ButtonRelease-1>", self._on_recipient_click)
        self.recipients.bind("<Motion>", self._on_recipient_motion)
        self.recipients.bind("<Delete>", lambda _e: self._remove_selected_recipient())

        add = ttk.Frame(frm)
        add.pack(fill="x", padx=16, pady=(8, 0))
        self.number_add = ttk.Entry(add)
        self.number_add.pack(side="left", fill="x", expand=True)
        self.number_add.bind("<Return>", lambda _e: self._add_typed_number())
        button(add, text="Hinzufügen", command=self._add_typed_number).pack(side="left", padx=(8, 0))
        button(add, text="Telefonbuch", command=self.open_phonebook).pack(side="left", padx=(8, 0))

        row = ttk.Frame(frm)
        row.pack(fill="x", padx=16, pady=(10, 0))
        ttk.Label(row, text="Absender").pack(side="left")
        self.sender = ttk.Entry(row)
        self.sender.pack(side="left", fill="x", expand=True, padx=(8, 0))
        from .cover import default_sender

        self.sender.insert(0, self.settings.sender_name or default_sender())

        self.cover_on = tk.BooleanVar(master=self.win, value=bool(self.settings.cover_enabled))
        self.cover_info = tk.BooleanVar(master=self.win, value=bool(self.settings.cover_show_info))
        self.cover_on_chk = ttk.Checkbutton(
            frm,
            text="Leere Deckseite mit Sendeinfos einfügen",
            variable=self.cover_on,
            onvalue=True,
            offvalue=False,
            command=self._on_form_change,
        )
        self.cover_on_chk.pack(anchor="w", padx=16, pady=(12, 0))
        self.cover_info_chk = ttk.Checkbutton(
            frm,
            text="Nummer, Absender und Seiten auf der Deckseite",
            variable=self.cover_info,
            onvalue=True,
            offvalue=False,
            command=self._on_form_change,
        )
        self.cover_info_chk.pack(anchor="w", padx=32, pady=(4, 0))

        ttk.Label(frm, text="Nachricht auf der Deckseite (optional):").pack(anchor="w", padx=16, pady=(10, 0))
        self.message = tk.Text(frm, height=4, wrap="word")
        self.message.pack(fill="both", expand=True, padx=16, pady=8)
        style_text(self.message)
        self.message.bind("<KeyRelease>", lambda _e: self._invalidate_preview())

    def _build_preview(self) -> None:
        ttk = self.ttk
        frm = self.preview
        nav = ttk.Frame(frm)
        nav.pack(fill="x", padx=16, pady=(14, 6))
        button(nav, text="◀", command=lambda: self._turn_page(-1), compact=True).pack(side="left")
        self.page_lbl = ttk.Label(nav, text="Seite —")
        self.page_lbl.pack(side="left", padx=8)
        button(nav, text="▶", command=lambda: self._turn_page(1), compact=True).pack(side="left")

        zoom = ttk.Frame(nav)
        zoom.pack(side="left", padx=(16, 0))
        button(zoom, text="−", command=lambda: self._zoom_delta(-1), compact=True).pack(side="left")
        self.zoom_lbl = ttk.Label(zoom, text="Fenstergröße", width=14, anchor="center")
        self.zoom_lbl.pack(side="left", padx=6)
        button(zoom, text="+", command=lambda: self._zoom_delta(1), compact=True).pack(side="left")
        button(zoom, text="An Fenstergröße anpassen", command=lambda: self._set_zoom(None)).pack(side="left", padx=(8, 0))

        self.preview_status = ttk.Label(nav, text="", style="Muted.TLabel")
        self.preview_status.pack(side="right")

        body = ttk.Frame(frm)
        body.pack(fill="both", expand=True, padx=16, pady=4)
        self.canvas = self.tk.Canvas(body, background="#ffffff", highlightthickness=0)
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_delta(1) or "break")
        self.canvas.bind("<Button-5>", lambda e: self._zoom_delta(-1) or "break")
        self.canvas.bind("<Control-MouseWheel>", lambda e: self._zoom_delta(1 if e.delta > 0 else -1) or "break")
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", padx=16, pady=(4, 16))
        button(btns, text="Zurück", command=self.show_main).pack(side="left")
        button(btns, text="Abbrechen", command=self.cancel).pack(side="right")
        self.preview_send_btn = button(btns, text="Senden", command=self.send, variant="primary")
        self.preview_send_btn.pack(side="right", padx=8)

    def _window_title(self) -> str:
        if self.origin == "app":
            return "Ricoh-LAN-Fax (App-Test)"
        return "Ricoh-LAN-Fax"

    def _size_to_main(self) -> None:
        self.win.update_idletasks()
        req_w = max(560, int(self.win.winfo_reqwidth()) + 32)
        req_h = max(760, int(self.win.winfo_reqheight()) + 32)
        self.win.minsize(560, 720)
        self.win.geometry(f"{req_w}x{req_h}")

    def _origin_text(self) -> str:
        if self.origin == "app":
            return "App-Test, nicht der Drucker"
        return "Drucker-Dialog"

    def _target_text(self) -> str:
        return f"Ricoh  ·  {self.settings.display_target()}"

    def open_settings(self) -> None:
        SettingsDialog(self.tk, self.ttk, self.messagebox, self.win, self.settings, self._saved)

    def open_phonebook(self) -> None:
        try:
            PhonebookDialog(self.tk, self.ttk, self.messagebox, self.win, self._apply_phonebook_numbers)
        except Exception as exc:  # noqa: BLE001
            self.messagebox.showerror("Telefonbuch", str(exc), parent=self.win)

    def _apply_phonebook_numbers(self, numbers: list[str]) -> None:
        self._add_recipients(numbers)

    def _redraw_recipients(self) -> None:
        for iid in self.recipients.get_children():
            self.recipients.delete(iid)
        for number, name in self._recipients:
            self.recipients.insert("", "end", iid=number, values=(number, name or "—", "✕"))

    def _add_recipients(self, numbers: list[str]) -> None:
        have = {item[0] for item in self._recipients}
        book = load_book()
        added = False
        for raw in numbers:
            compact = compact_number(raw)
            if not compact or compact in have:
                continue
            contact = book.lookup_number(compact)
            self._recipients.append((compact, contact.name if contact else ""))
            have.add(compact)
            added = True
        if added:
            self._redraw_recipients()
            self._invalidate_preview()

    def _add_typed_number(self) -> None:
        text = self.number_add.get()
        try:
            numbers = parse_numbers(text)
        except ValueError as exc:
            self.messagebox.showerror("Nummer", str(exc), parent=self.win)
            return
        if not numbers:
            return
        self._add_recipients(numbers)
        self.number_add.delete(0, "end")

    def _remove_recipient(self, number: str) -> None:
        before = len(self._recipients)
        self._recipients = [item for item in self._recipients if item[0] != number]
        if len(self._recipients) != before:
            self._redraw_recipients()
            self._invalidate_preview()

    def _remove_selected_recipient(self) -> None:
        selection = self.recipients.selection()
        if selection:
            self._remove_recipient(str(selection[0]))

    def _on_recipient_click(self, event: Any) -> None:
        if self.recipients.identify_region(event.x, event.y) != "cell":
            return
        row = self.recipients.identify_row(event.y)
        if not row:
            return
        if self.recipients.identify_column(event.x) == "#3":
            self._remove_recipient(str(row))

    def _on_recipient_motion(self, event: Any) -> None:
        row = self.recipients.identify_row(event.y)
        col = self.recipients.identify_column(event.x)
        self.recipients.configure(cursor="hand2" if row and col == "#3" else "")

    def _saved(self, settings: Settings) -> None:
        self.settings = settings
        self.target_lbl.config(text=self._target_text())

    def _read_numbers(self) -> list[str]:
        return [number for number, _name in self._recipients]

    def _cover_checked(self, var: Any, chk: Any) -> bool:
        try:
            self.win.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        try:
            if bool(var.get()):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            return bool(chk.instate(["selected"]))
        except Exception:  # noqa: BLE001
            return False

    def _cover_spec(self, numbers: list[str], document_pages: int = 0):
        from .cover import CoverSpec

        if not self._cover_checked(self.cover_on, self.cover_on_chk):
            return None
        return CoverSpec(
            numbers=list(numbers),
            sender=self.sender.get().strip(),
            hostname="",
            title=self.doc_title,
            message=self.message.get("1.0", "end").rstrip("\n"),
            show_info=self._cover_checked(self.cover_info, self.cover_info_chk),
            document_pages=document_pages,
        )

    def _invalidate_preview(self) -> None:
        self._preview_gen += 1
        self._photo_src = None
        self._photo_src_path = None

    def _on_form_change(self) -> None:
        self._invalidate_preview()
        self._persist_form()

    def _persist_form(self, _numbers: list[str] | None = None) -> None:
        settings = load_settings()
        settings.printer_host = self.settings.printer_host
        settings.printer_port = self.settings.printer_port
        settings.sender_name = self.sender.get().strip()
        settings.cover_enabled = self._cover_checked(self.cover_on, self.cover_on_chk)
        settings.cover_show_info = self._cover_checked(self.cover_info, self.cover_info_chk)
        settings.last_numbers = []
        settings.cover_message = ""
        settings.preview_zoom = "fit" if self._zoom is None else str(self._zoom)
        save_settings(settings)
        self.settings = settings

    def show_main(self) -> None:
        self._invalidate_preview()
        self._persist_form()
        self.preview.pack_forget()
        self.main.pack(fill="both", expand=True)
        self.win.title(self._window_title())
        self._size_to_main()
        self.status.config(text="")

    def show_preview(self) -> None:
        try:
            numbers = self._read_numbers()
        except ValueError as exc:
            self.messagebox.showerror("Nummer", str(exc), parent=self.win)
            return
        self.win.update_idletasks()
        self._persist_form(numbers)
        cover = self._cover_spec(numbers)
        document = self.document
        self._preview_gen += 1
        gen = self._preview_gen
        self.status.config(text="Erzeuge Vorschau …")

        def work() -> None:
            try:
                from .cover import render_preview_pngs

                cache = self.project / "preview" / f"run-{gen}"
                files = render_preview_pngs(document, cover, cache)
                self.win.after(0, lambda: self._open_preview(files, gen))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                self.win.after(0, lambda m=msg, g=gen: self._preview_fail(m, g))

        threading.Thread(target=work, daemon=True).start()

    def _preview_fail(self, message: str, gen: int | None = None) -> None:
        if gen is not None and gen != self._preview_gen:
            return
        self.status.config(text=message)
        self.messagebox.showerror("Vorschau", message, parent=self.win)

    def _open_preview(self, files: list[Path], gen: int | None = None) -> None:
        if gen is not None and gen != self._preview_gen:
            return
        self.status.config(text="")
        if not files:
            self.messagebox.showinfo(
                "Vorschau",
                "Keine Seiten zum Anzeigen (Binär-Vorlage ohne PDF). Senden geht trotzdem.",
                parent=self.win,
            )
            return
        self._photo_src = None
        self._photo_src_path = None
        self._preview_files = files
        self._preview_index = 0
        self.main.pack_forget()
        self.preview.pack(fill="both", expand=True)
        self.win.title("Vorschau — " + self._window_title())
        self.win.geometry("640x860")
        self._update_zoom_label()
        self._draw_page()

    def _turn_page(self, delta: int) -> None:
        if not self._preview_files:
            return
        self._preview_index = (self._preview_index + delta) % len(self._preview_files)
        self._draw_page()

    def _on_canvas_configure(self, event: Any) -> None:
        size = (int(event.width), int(event.height))
        if size == self._last_canvas:
            return
        self._last_canvas = size
        if self._zoom is None:
            self._draw_page()

    def _update_zoom_label(self) -> None:
        if not hasattr(self, "zoom_lbl"):
            return
        self.zoom_lbl.config(text="Fenstergröße" if self._zoom is None else f"{self._zoom} %")

    def _set_zoom(self, percent: int | None) -> None:
        self._zoom = percent
        self._update_zoom_label()
        self._persist_form()
        self._draw_page()

    def _zoom_delta(self, step: int) -> None:
        if not self._preview_files:
            return
        current = 100 if self._zoom is None else self._zoom
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
        nxt = max(0, min(len(ZOOM_STEPS) - 1, idx + step))
        self._set_zoom(ZOOM_STEPS[nxt])

    def _draw_page(self) -> None:
        if not self._preview_files:
            return
        path = self._preview_files[self._preview_index]
        n = len(self._preview_files)
        self.page_lbl.config(text=f"Seite {self._preview_index + 1} / {n}")
        cw = max(1, int(self.canvas.winfo_width() or 500))
        ch = max(1, int(self.canvas.winfo_height() or 640))
        box = (max(32, cw - 20), max(32, ch - 20))
        if self._zoom is None:
            img = _resize_preview(path, self.win, box, 100)
            percent_note = "Fenstergröße"
        else:
            img = _resize_preview(path, self.win, None, self._zoom)
            percent_note = f"{self._zoom} %"
        if img is None:
            if self._photo_src_path != path or self._photo_src is None:
                try:
                    self._photo_src = self.tk.PhotoImage(
                        file=str(path),
                        name=f"faxprev-{self._preview_gen}-{self._preview_index}-{id(path)}",
                    )
                    self._photo_src_path = path
                except self.tk.TclError:
                    self.preview_status.config(text="Bild nicht lesbar")
                    return
            src = self._photo_src
            if self._zoom is None:
                img = _tk_fit_photo(src, box[0], box[1])
            else:
                img = _scale_photo(src, self._zoom) if self._zoom != 100 else src
        self._photo = img
        self.canvas.delete("all")
        iw, ih = img.width(), img.height()
        if self._zoom is None:
            self.canvas.create_image(cw // 2, ch // 2, image=img, anchor="center")
            self.canvas.configure(scrollregion=(0, 0, cw, ch))
        else:
            self.canvas.create_image(0, 0, image=img, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, iw, ih))
        self._update_zoom_label()
        self.preview_status.config(text=f"{path.name}  ·  {percent_note}")

    def cancel(self) -> None:
        self._persist_form()
        if self.on_done:
            self.on_done("cancel", "")
        self.win.destroy()

    def send(self) -> None:
        if self._sending:
            return
        try:
            numbers = self._read_numbers()
        except ValueError as exc:
            self.messagebox.showerror("Nummer", str(exc), parent=self.win)
            return
        if not numbers:
            self.messagebox.showerror("Nummer", "Mindestens eine Faxnummer eintragen.", parent=self.win)
            return
        if self.document is None or not self.document.exists():
            self.messagebox.showerror("Dokument", "Keine Datei zum Senden.", parent=self.win)
            return
        self._persist_form(numbers)
        book = load_book()
        book.record_sent(numbers)
        save_book(book)
        self.settings = load_settings()
        if not self.settings.printer_host.strip():
            self.open_settings()
            return
        self._sending = True
        self.send_btn.config(state="disabled")
        self.preview_send_btn.config(state="disabled")
        self.status.config(text="Sende …")
        self.preview_status.config(text="Sende …")
        settings = self.settings
        document = self.document
        self.win.update_idletasks()
        cover = self._cover_spec(numbers)
        dump = (self.project / "captures") if settings.save_debug_dump else None

        def work() -> None:
            from .send import PartialSendError, build_job, send_document, send_raw, write_debug_dump

            try:
                if document.suffix.lower() == ".raw":
                    results: list[tuple[str, bool, str]] = []
                    last = b""
                    for number in numbers:
                        try:
                            job, _note = build_job(None, number, template=document)
                            last = job
                            send_raw(settings.printer_host.strip(), settings.printer_port, job)
                            results.append((number, True, ""))
                        except Exception as exc:  # noqa: BLE001
                            results.append((number, False, str(exc)))
                    if dump is not None and last:
                        write_debug_dump(dump / "linux-last.raw", last)
                    if any(not ok for _n, ok, _e in results):
                        raise PartialSendError(results)
                    note = (
                        f"{len(numbers)} Empfänger → {settings.printer_host.strip()}:"
                        f"{settings.printer_port}: An Gerät übergeben"
                    )
                else:
                    note = send_document(
                        document,
                        numbers,
                        settings.printer_host,
                        settings.printer_port,
                        dump_dir=dump,
                        cover=cover,
                    )
                self.win.after(0, lambda: self._ok(note))
            except PartialSendError as exc:
                self.win.after(0, lambda e=exc: self._partial_fail(e))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                self.win.after(0, lambda m=msg: self._fail(m))

        threading.Thread(target=work, daemon=True).start()

    def _ok(self, note: str) -> None:
        self.status.config(text=note)
        if self.on_done:
            self.on_done("ok", note)
        self.win.destroy()

    def _partial_fail(self, exc: object) -> None:
        results = getattr(exc, "results", [])
        delivered = [number for number, ok, _err in results if ok]
        failed = [(number, err) for number, ok, err in results if not ok]
        for number in delivered:
            self._remove_recipient(number)
        ok_txt = ", ".join(delivered) if delivered else "keine"
        fail_txt = ", ".join(f"{number} ({err})" for number, err in failed)
        message = (
            f"Teilweise an das Gerät übergeben.\n"
            f"Übergeben: {ok_txt}\n"
            f"Nicht übergeben: {fail_txt}"
        )
        self._fail(message)

    def _fail(self, message: str) -> None:
        self._sending = False
        self.send_btn.config(state="normal")
        self.preview_send_btn.config(state="normal")
        self.status.config(text=message)
        self.preview_status.config(text=message)
        self.messagebox.showerror("Senden", message, parent=self.win)


class SinkGui:
    def __init__(self, tk: Any, ttk: Any, scrolledtext: Any, messagebox: Any, filedialog: Any, root: Any, project: Path) -> None:
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.filedialog = filedialog
        self.root = root
        self.project = project
        self.out_dir = project / "captures"
        self.servers: list[threading.Thread] = []
        self.log_q: queue.Queue[str] = queue.Queue()
        self.port = 9100
        self.send_file: Path | None = None
        self._open_jobs: set[str] = set()
        self.settings = load_settings()

        root.title("Ricoh LAN-Fax Labor")
        root.geometry("760x640")
        root.minsize(620, 500)
        apply_theme(root)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        fax = ttk.Frame(nb)
        lab = ttk.Frame(nb)
        nb.add(fax, text="Fax-Drucker")
        nb.add(lab, text="Labor (Windows-Capture)")
        self._build_fax_tab(fax)
        self._build_lab_tab(lab, scrolledtext)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._drain_log)
        root.after(400, self._poll_spool)

    def _build_fax_tab(self, parent: Any) -> None:
        hint = (
            "Drucken auf den Ubuntu-Drucker „Ricoh-LAN-Fax“.\n"
            "Das Nummern-Fenster kommt nur vom Drucker-Backend — diese App darf nicht mitlaufen,\n"
            "sonst öffnet sie ein zweites, veraltetes Fenster.\n"
            "Nach Code-Updates: sudo ./install-printer.sh  und diese App schließen."
        )
        self.ttk.Label(parent, text=hint, justify="left", style="Muted.TLabel").pack(anchor="w", padx=16, pady=(16, 8))

        bar = self.ttk.Frame(parent)
        bar.pack(fill="x", padx=16)
        self.fax_target = self.ttk.Label(bar, text=f"Ziel: {self.settings.display_target()}", style="Title.TLabel")
        self.fax_target.pack(side="left")
        button(bar, text="⚙  Ricoh-IP", command=lambda: self.open_settings(parent)).pack(side="right")

        btns = self.ttk.Frame(parent)
        btns.pack(fill="x", padx=16, pady=12)
        button(btns, text="PDF jetzt als Fax (ohne CUPS)", command=self.manual_fax).pack(side="left")
        button(btns, text="Popup testen", command=self.test_popup, variant="primary").pack(side="left", padx=8)

        self.fax_log = self.tk.Text(parent, height=16, wrap="word")
        self.fax_log.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        style_text(self.fax_log, mono=True)
        self._fax("Bereit. Diese App ist nur Labor — beim Drucken das Fenster schließen.")
        self._fax("Das Fax-Popup kommt vom Drucker „Ricoh-LAN-Fax“, Titel: Ricoh-LAN-Fax.")
        if not self.settings.printer_host.strip():
            self._fax("Noch keine IM-350F-IP — oben rechts ⚙.")

    def _build_lab_tab(self, parent: Any, scrolledtext: Any) -> None:
        hint = (
            "Sink: Windows-VM faxt auf diesen Rechner (Raw 9100).\n"
            "Jobs landen in captures/ als .raw / .pjl.txt / .pcap"
        )
        self.ttk.Label(parent, text=hint, justify="left", style="Muted.TLabel").pack(anchor="w", padx=16, pady=(16, 4))
        ips = ", ".join(list_ipv4())
        self.status = self.ttk.Label(parent, text=f"Sink gestoppt  ·  IPs: {ips}  ·  Port {self.port}", style="Muted.TLabel")
        self.status.pack(anchor="w", padx=16, pady=4)
        btns = self.ttk.Frame(parent)
        btns.pack(fill="x", padx=16, pady=8)
        self.btn_start = button(btns, text="Sink starten", command=self.start, variant="primary")
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = button(btns, text="Stoppen", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        button(btns, text="Captures öffnen", command=self.open_captures).pack(side="left", padx=6)
        button(btns, text="Letzten Job lesen", command=self.parse_latest).pack(side="left", padx=6)
        self.log = scrolledtext.ScrolledText(parent, height=16, wrap="word")
        self.log.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        style_text(self.log, mono=True)

    def _fax(self, line: str) -> None:
        self.fax_log.insert("end", line.rstrip() + "\n")
        self.fax_log.see("end")

    def _append(self, line: str) -> None:
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")

    def _gui_log(self, message: str) -> None:
        self.log_q.put(message)

    def _drain_log(self) -> None:
        try:
            while True:
                self._append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(200, self._drain_log)

    def _refresh_target(self) -> None:
        self.settings = load_settings()
        self.fax_target.config(text=f"Ziel: {self.settings.display_target()}")

    def open_settings(self, parent: Any) -> None:
        SettingsDialog(self.tk, self.ttk, self.messagebox, parent.winfo_toplevel(), load_settings(), lambda _s: self._refresh_target())

    def _poll_spool(self) -> None:
        try:
            for job in pending_jobs():
                jid = str(job.get("job_id") or "")
                if not jid or jid in self._open_jobs:
                    continue
                self._open_jobs.add(jid)
                self._fax(
                    f"Auftrag {jid} ({job.get('title')}): Popup kommt vom Drucker, nicht von dieser App."
                )
        except Exception as exc:  # noqa: BLE001
            self._fax(f"Spool: {exc}")
        self.root.after(500, self._poll_spool)

    def manual_fax(self) -> None:
        path = self.filedialog.askopenfilename(
            title="PDF oder TIFF als Fax",
            filetypes=[("Dokumente", "*.pdf *.tif *.tiff"), ("PDF", "*.pdf"), ("Alles", "*")],
        )
        if not path:
            return
        doc = Path(path)
        FaxPopup(self.tk, self.ttk, self.messagebox, self.root, self.project, None, doc, doc.name, None, origin="app")

    def test_popup(self) -> None:
        raw = latest_raw(self.out_dir)
        if raw is None:
            self.messagebox.showinfo("Test", "Kein Capture zum Testen. PDF über „PDF jetzt als Fax“ wählen.")
            return
        FaxPopup(self.tk, self.ttk, self.messagebox, self.root, self.project, None, raw, "Test (Capture-Vorlage)", None, origin="app")

    def start(self) -> None:
        if self.servers:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.servers = start_servers("0.0.0.0", self.port, self.out_dir, self._gui_log)
        ips = ", ".join(list_ipv4())
        self.status.config(text=f"Sink läuft  ·  Windows-Port = {ips}  ·  Raw {self.port}")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def stop(self) -> None:
        if not self.servers:
            return
        stop_servers(self.servers, self._gui_log)
        self.servers = []
        ips = ", ".join(list_ipv4())
        self.status.config(text=f"Sink gestoppt  ·  IPs: {ips}  ·  Port {self.port}")
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def open_captures(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(self.out_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def parse_latest(self) -> None:
        raw = latest_raw(self.out_dir)
        if not raw:
            self.messagebox.showinfo("Kein Job", "Noch keine .raw in captures/.")
            return
        report = format_report(parse_job(raw.read_bytes()))
        self._append(f"\n===== {raw.name} =====")
        self._append(report)

    def on_close(self) -> None:
        self.stop()
        self.root.destroy()


def run_gui(project: Path | None = None) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError:
        print("Tkinter fehlt. Auf Ubuntu: sudo apt install python3-tk", file=sys.stderr)
        print("Oder nur Terminal: ./start.sh --terminal", file=sys.stderr)
        return 1
    root_dir = project or Path(__file__).resolve().parents[1]
    root = tk.Tk()
    SinkGui(tk, ttk, scrolledtext, messagebox, filedialog, root, root_dir)
    root.mainloop()
    return 0
