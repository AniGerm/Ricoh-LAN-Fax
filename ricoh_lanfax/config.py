"""User settings: Ricoh IP, port, last fax numbers, cover page."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

NUMBER_CHARS = re.compile(r"[0-9+#*P\-]+")


def config_path() -> Path:
    return Path.home() / ".config" / "ricoh-lanfax" / "config.json"


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "ja"}
    if value is None:
        return default
    return bool(value)


@dataclass
class Settings:
    printer_host: str = ""
    printer_port: int = 9100
    last_numbers: list[str] = field(default_factory=list)
    sender_name: str = ""
    cover_enabled: bool = False
    cover_show_info: bool = True
    cover_message: str = ""
    preview_zoom: str = "fit"
    save_debug_dump: bool = False

    def display_target(self) -> str:
        host = self.printer_host.strip() or "(keine IP)"
        return f"{host}:{self.printer_port}"


def load_settings() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    port = data.get("printer_port", 9100)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 9100
    numbers = data.get("last_numbers") or []
    if not isinstance(numbers, list):
        numbers = []
    return Settings(
        printer_host=str(data.get("printer_host") or ""),
        printer_port=port,
        last_numbers=[str(n) for n in numbers if str(n).strip()],
        sender_name=str(data.get("sender_name") or ""),
        cover_enabled=_as_bool(data.get("cover_enabled"), False),
        cover_show_info=_as_bool(data.get("cover_show_info"), True),
        cover_message=str(data.get("cover_message") or ""),
        preview_zoom=str(data.get("preview_zoom") or "fit"),
        save_debug_dump=_as_bool(data.get("save_debug_dump"), False),
    )


def save_settings(settings: Settings) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


def parse_numbers(text: str) -> list[str]:
    """Split fax numbers: newline, comma, or semicolon. Keep + # * P and digits."""
    out: list[str] = []
    for part in re.split(r"[\n,;]+", text):
        compact = "".join(part.split())
        if not compact:
            continue
        if not NUMBER_CHARS.fullmatch(compact):
            raise ValueError(f"Ungültige Nummer: {part.strip()!r}")
        if compact not in out:
            out.append(compact)
    return out
