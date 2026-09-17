"""Light Apple/Google-like Tk theme: pale surfaces, navy rounded buttons."""

from __future__ import annotations

from typing import Any, Callable

BG = "#F4F6F8"
SURFACE = "#FFFFFF"
TEXT = "#1D1D1F"
MUTED = "#6E6E73"
NAVY = "#0F3D6E"
NAVY_HOVER = "#16518F"
NAVY_DOWN = "#0B2F55"
CHIP = "#E6EAF0"
CHIP_HOVER = "#D5DCE6"
CHIP_DOWN = "#C5CEDA"
BORDER = "#D5DBE3"
SELECT = "#D9E6F5"
HEAD = "#EEF1F5"
DISABLED_BG = "#C5CDD8"
DISABLED_FG = "#F7F8FA"

_FONT_CANDIDATES = ("Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans", "Segoe UI", "Helvetica")


def _font_family(root: Any) -> str:
    try:
        families = set(root.tk.call("font", "families"))
    except Exception:  # noqa: BLE001
        return "Helvetica"
    for name in _FONT_CANDIDATES:
        if name in families:
            return name
    return "Helvetica"


def apply_theme(root: Any) -> None:
    """Apply once per Tk app; safe to call again on Toplevels."""
    import tkinter.font as tkfont
    from tkinter import ttk

    style_window(root)
    app = root.nametowidget(".")
    if getattr(app, "_ricoh_theme", False):
        return
    setattr(app, "_ricoh_theme", True)

    family = _font_family(root)
    for name, size, weight in (
        ("TkDefaultFont", 11, "normal"),
        ("TkTextFont", 11, "normal"),
        ("TkMenuFont", 11, "normal"),
        ("TkHeadingFont", 12, "bold"),
    ):
        try:
            tkfont.nametofont(name).configure(family=family, size=size, weight=weight)
        except Exception:  # noqa: BLE001
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass

    font = (family, 11)
    font_bold = (family, 11, "bold")
    style.configure(".", background=BG, foreground=TEXT, font=font, troughcolor=CHIP)
    style.configure("TFrame", background=BG)
    style.configure("TLabelframe", background=BG, foreground=TEXT)
    style.configure("TLabel", background=BG, foreground=TEXT, font=font)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=font)
    style.configure("Title.TLabel", background=BG, foreground=NAVY, font=(family, 13, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=font, indicatorbackground=SURFACE, indicatormargin=4)
    style.map("TCheckbutton", indicatorcolor=[("selected", NAVY), ("!selected", SURFACE)], background=[("active", BG)])
    style.configure("TRadiobutton", background=BG, foreground=TEXT, font=font, indicatorbackground=SURFACE)
    style.map("TRadiobutton", indicatorcolor=[("selected", NAVY), ("!selected", SURFACE)], background=[("active", BG)])
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=NAVY,
        darkcolor=BORDER,
        padding=(10, 7),
        relief="flat",
    )
    style.map("TEntry", bordercolor=[("focus", NAVY)], lightcolor=[("focus", NAVY)])
    style.configure(
        "TButton",
        background=NAVY,
        foreground=SURFACE,
        font=font_bold,
        padding=(14, 8),
        borderwidth=0,
        focusthickness=0,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", NAVY_DOWN), ("active", NAVY_HOVER), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        font=font,
        rowheight=30,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        relief="flat",
        padding=0,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background=HEAD,
        foreground=MUTED,
        font=font_bold,
        relief="flat",
        borderwidth=0,
        padding=(0, 6),
    )
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", NAVY)])
    style.map("Treeview.Heading", background=[("active", CHIP)])
    try:
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    except Exception:  # noqa: BLE001
        pass
    style.configure("TScrollbar", background=CHIP, troughcolor=BG, bordercolor=BG, arrowcolor=NAVY, relief="flat")
    style.map("TScrollbar", background=[("active", CHIP_HOVER)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=CHIP, foreground=TEXT, padding=(16, 8), font=font, borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", NAVY)])


def style_window(win: Any) -> None:
    try:
        win.configure(bg=BG)
    except Exception:  # noqa: BLE001
        pass


def prepare_treeview(tree: Any, columns: list[tuple[str, str, int, bool, str]]) -> None:
    """Hide the phantom #0 column and keep heading/cell anchors identical."""
    tree.column("#0", width=0, minwidth=0, stretch=False)
    try:
        tree.configure(indent=0)
    except Exception:  # noqa: BLE001
        pass
    ids = [col_id for col_id, _title, _width, _stretch, _anchor in columns]
    tree.configure(columns=ids, show="headings")
    for col_id, title, width, stretch, anchor in columns:
        tree.heading(col_id, text=title, anchor=anchor)
        minwidth = width if not stretch else max(48, width // 2)
        tree.column(col_id, width=width, minwidth=minwidth, stretch=stretch, anchor=anchor)


def style_text(widget: Any, *, mono: bool = False) -> None:
    family = "DejaVu Sans Mono" if mono else _font_family(widget)
    widget.configure(
        background=SURFACE,
        foreground=TEXT,
        insertbackground=TEXT,
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=NAVY,
        padx=10,
        pady=8,
        font=(family, 10 if mono else 11),
    )


def _round_fill(canvas: Any, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str) -> list[int]:
    r = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    return [
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="", width=0),
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="", width=0),
        canvas.create_oval(x1, y1, x1 + 2 * r, y1 + 2 * r, fill=fill, outline="", width=0),
        canvas.create_oval(x2 - 2 * r, y1, x2, y1 + 2 * r, fill=fill, outline="", width=0),
        canvas.create_oval(x1, y2 - 2 * r, x1 + 2 * r, y2, fill=fill, outline="", width=0),
        canvas.create_oval(x2 - 2 * r, y2 - 2 * r, x2, y2, fill=fill, outline="", width=0),
    ]


class RoundedButton:
    """Canvas pill button; pack/grid like a ttk.Button, plus config(state=)."""

    def __init__(
        self,
        master: Any,
        text: str = "",
        command: Callable[[], Any] | None = None,
        variant: str = "secondary",
        compact: bool = False,
        state: str = "normal",
        **_ignored: Any,
    ) -> None:
        import tkinter as tk
        import tkinter.font as tkfont

        self._command = command
        self._text = text
        self._variant = variant if variant in {"primary", "secondary"} else "secondary"
        self._compact = compact
        self._state = state
        self._hover = False
        self._pressed = False
        family = _font_family(master)
        size = 10 if compact else 11
        weight = "bold" if not compact else "normal"
        self._font = tkfont.Font(family=family, size=size, weight=weight)
        pad_x = 10 if compact else 16
        height = 32 if compact else 36
        width = max(height, self._font.measure(text) + pad_x * 2)
        self.widget = tk.Canvas(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=BG,
            cursor="hand2",
        )
        self._canvas = self.widget
        self._items: list[int] = []
        self._redraw()
        self.widget.bind("<Enter>", self._enter)
        self.widget.bind("<Leave>", self._leave)
        self.widget.bind("<ButtonPress-1>", self._down)
        self.widget.bind("<ButtonRelease-1>", self._up)

    def pack(self, **kw: Any) -> Any:
        return self.widget.pack(**kw)

    def grid(self, **kw: Any) -> Any:
        return self.widget.grid(**kw)

    def place(self, **kw: Any) -> Any:
        return self.widget.place(**kw)

    def config(self, **kw: Any) -> Any:
        if "state" in kw:
            self._state = str(kw.pop("state"))
        if "text" in kw:
            self._text = str(kw.pop("text"))
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            self.widget.config(**kw)
        self._redraw()
        return self

    configure = config

    def _colors(self) -> tuple[str, str]:
        disabled = self._state in {"disabled", "disable"}
        if self._variant == "primary":
            if disabled:
                return DISABLED_BG, DISABLED_FG
            if self._pressed:
                return NAVY_DOWN, SURFACE
            if self._hover:
                return NAVY_HOVER, SURFACE
            return NAVY, SURFACE
        if disabled:
            return CHIP, MUTED
        if self._pressed:
            return CHIP_DOWN, TEXT
        if self._hover:
            return CHIP_HOVER, TEXT
        return CHIP, TEXT

    def _redraw(self) -> None:
        c = self.widget
        for item in self._items:
            c.delete(item)
        self._items.clear()
        w = int(c.cget("width"))
        h = int(c.cget("height"))
        fill, fg = self._colors()
        radius = h // 2 if self._compact else 12
        self._items.extend(_round_fill(c, 1, 1, w - 1, h - 1, radius, fill))
        tid = c.create_text(w // 2, h // 2, text=self._text, fill=fg, font=self._font)
        self._items.append(tid)
        c.configure(cursor="" if self._state in {"disabled", "disable"} else "hand2")

    def _enter(self, _event: Any) -> None:
        self._hover = True
        self._redraw()

    def _leave(self, _event: Any) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _down(self, _event: Any) -> None:
        if self._state in {"disabled", "disable"}:
            return
        self._pressed = True
        self._redraw()

    def _up(self, _event: Any) -> None:
        if self._state in {"disabled", "disable"}:
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._command:
            self._command()


def button(
    master: Any,
    text: str = "",
    command: Callable[[], Any] | None = None,
    variant: str = "secondary",
    compact: bool = False,
    state: str = "normal",
    **kw: Any,
) -> RoundedButton:
    return RoundedButton(master, text=text, command=command, variant=variant, compact=compact, state=state, **kw)
