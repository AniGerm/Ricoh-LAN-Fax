"""Fax address book: local JSON plus LDAP / vCard (NovaMail-compatible)."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from .config import NUMBER_CHARS, config_path, load_settings, parse_numbers

RECENT_LIMIT = 50
SEARCH_FLOOR = 0.42
MODE_LOCAL = "local"
MODE_DIRECTORY = "directory"

_UMLAUT = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "Ä": "ae",
        "Ö": "oe",
        "Ü": "ue",
    }
)


def phonebook_path() -> Path:
    return config_path().parent / "phonebook.json"


def compact_number(value: str) -> str:
    return "".join(str(value).split())


def digits_only(value: str) -> str:
    return re.sub(r"\D+", "", compact_number(value))


def fold_text(value: str) -> str:
    raw = str(value).translate(_UMLAUT)
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_ish = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return ascii_ish.casefold().strip()


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    i = 0
    for ch in haystack:
        if ch == needle[i]:
            i += 1
            if i >= len(needle):
                return True
    return False


def sort_name_key(name: str) -> str:
    return fold_text(name)


@dataclass
class Contact:
    id: str
    name: str
    number: str
    favorite: bool = False
    source: str = "local"
    notes: str = ""

    def compact(self) -> str:
        return compact_number(self.number)

    def label(self) -> str:
        return self.name.strip() or self.number


@dataclass
class Recent:
    number: str
    used_at: str = ""
    count: int = 1

    def compact(self) -> str:
        return compact_number(self.number)


class AddressBookSource(Protocol):
    """Plug-in directory (LDAP, vCard, …). Local contacts stay writable separately."""

    source_id: str
    label: str
    writable: bool

    def list_contacts(self) -> list[Contact]:
        ...

    def search(self, query: str) -> list[Contact]:
        ...


DEFAULT_SOURCES: dict[str, Any] = {
    "mode": MODE_LOCAL,
    "ldap": {
        "enabled": False,
        "url": "",
        "bind_dn": "",
        "bind_password": "",
        "base_dn": "",
        "filter": "(objectClass=inetOrgPerson)",
        "name_attr": "cn",
        "number_attrs": ["facsimileTelephoneNumber", "telephoneNumber", "fax", "mobile"],
        "preset": "",
    },
    "vcard": {
        "enabled": False,
        "path": "",
        "username": "",
        "password": "",
    },
}


def addressbook_mode(config: dict[str, Any] | None = None) -> str:
    cfg = config if isinstance(config, dict) else DEFAULT_SOURCES
    mode = str(cfg.get("mode") or MODE_LOCAL).strip().lower()
    if mode in {MODE_DIRECTORY, "ldap", "vcard", "novamail", "remote"}:
        return MODE_DIRECTORY
    return MODE_LOCAL


def build_extra_sources(config: dict[str, Any]) -> list[AddressBookSource]:
    from .directory import LdapSource, VcardSource

    mode = addressbook_mode(config)
    directory_on = mode == MODE_DIRECTORY
    extra: list[AddressBookSource] = []
    ldap_cfg = dict(DEFAULT_SOURCES["ldap"])
    ldap_cfg.update(config.get("ldap") or {})
    ldap_enabled = directory_on and bool(ldap_cfg.get("enabled"))
    extra.append(
        LdapSource(
            enabled=ldap_enabled,
            url=str(ldap_cfg.get("url") or ""),
            bind_dn=str(ldap_cfg.get("bind_dn") or ""),
            bind_password=str(ldap_cfg.get("bind_password") or ""),
            base_dn=str(ldap_cfg.get("base_dn") or ""),
            filter=str(ldap_cfg.get("filter") or DEFAULT_SOURCES["ldap"]["filter"]),
            name_attr=str(ldap_cfg.get("name_attr") or "cn"),
            number_attrs=[
                str(a) for a in (ldap_cfg.get("number_attrs") or DEFAULT_SOURCES["ldap"]["number_attrs"])
            ],
        )
    )
    vcard_cfg = dict(DEFAULT_SOURCES["vcard"])
    vcard_cfg.update(config.get("vcard") or {})
    vcard_enabled = directory_on and bool(vcard_cfg.get("enabled"))
    extra.append(
        VcardSource(
            enabled=vcard_enabled,
            path=str(vcard_cfg.get("path") or ""),
            username=str(vcard_cfg.get("username") or ""),
            password=str(vcard_cfg.get("password") or ""),
        )
    )
    return extra


def fuzzy_score(query: str, name: str, number: str) -> float:
    q_raw = query.strip()
    if not q_raw:
        return 1.0
    q = fold_text(q_raw)
    q_digits = digits_only(q_raw)
    folded_name = fold_text(name)
    num_digits = digits_only(number)
    score = 0.0

    if q_digits and num_digits:
        if num_digits.startswith(q_digits) or q_digits.startswith(num_digits) and len(num_digits) >= 3:
            score = max(score, 1.0)
        elif q_digits in num_digits:
            score = max(score, 0.93)
        else:
            score = max(score, SequenceMatcher(None, q_digits, num_digits).ratio() * 0.88)

    if q:
        if folded_name.startswith(q):
            score = max(score, 0.99)
        elif q in folded_name:
            score = max(score, 0.9)
        else:
            if _is_subsequence(q, folded_name):
                score = max(score, 0.72)
            score = max(score, SequenceMatcher(None, q, folded_name).ratio())
            for token in folded_name.replace("-", " ").split():
                if token.startswith(q):
                    score = max(score, 0.96)
                score = max(score, SequenceMatcher(None, q, token).ratio() * 0.94)
    return score


@dataclass
class AddressBook:
    contacts: list[Contact] = field(default_factory=list)
    recents: list[Recent] = field(default_factory=list)
    sources_config: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_SOURCES)))
    extra_sources: list[AddressBookSource] = field(default_factory=list)

    def mode(self) -> str:
        return addressbook_mode(self.sources_config)

    def directory(self, *, remote: bool = True) -> list[Contact]:
        # Local mode: only local contacts. Directory mode: LDAP/vCard (+ keep local favorites).
        if self.mode() == MODE_LOCAL or not remote:
            return list(self.contacts)
        found: list[Contact] = list(self.contacts)
        seen = {c.compact() for c in found}
        for src in self.extra_sources:
            if not getattr(src, "enabled", False):
                continue
            # Skip remote sources that have not been warmed up yet (UI stays responsive).
            if getattr(src, "_cache", None) is None and not getattr(src, "_error", ""):
                continue
            for contact in src.list_contacts():
                key = contact.compact()
                if key in seen:
                    continue
                seen.add(key)
                found.append(contact)
        return found

    def named_sorted(self, *, remote: bool = True) -> list[Contact]:
        named = [c for c in self.directory(remote=remote) if c.name.strip()]
        named.sort(key=lambda c: (sort_name_key(c.name), c.compact()))
        return named

    def favorites(self, *, remote: bool = True) -> list[Contact]:
        return [c for c in self.named_sorted(remote=remote) if c.favorite]

    def lookup_number(self, number: str) -> Contact | None:
        key = compact_number(number)
        for contact in self.directory():
            if contact.compact() == key:
                return contact
        return None

    def warmup_remote(self) -> None:
        """Fetch LDAP/vCard into source caches (safe to call from a worker thread)."""
        if self.mode() != MODE_DIRECTORY:
            return
        for src in self.extra_sources:
            if not getattr(src, "enabled", False):
                continue
            try:
                src.list_contacts()
            except Exception:  # noqa: BLE001
                pass

    def search(self, query: str, *, favorite_only: bool = False, remote: bool = True) -> list[tuple[float, Contact]]:
        q = query.strip()
        rows: list[tuple[float, Contact]] = []
        for contact in self.directory(remote=remote):
            if favorite_only and not contact.favorite:
                continue
            score = fuzzy_score(q, contact.name, contact.number)
            if score >= SEARCH_FLOOR:
                rows.append((score, contact))
        if remote:
            for src in self.extra_sources:
                if not getattr(src, "enabled", False):
                    continue
                if getattr(src, "_cache", None) is None and not getattr(src, "_error", ""):
                    continue
                if q:
                    for contact in src.search(q):
                        if favorite_only and not contact.favorite:
                            continue
                        if any(c.id == contact.id for _, c in rows):
                            continue
                        rows.append((max(fuzzy_score(q, contact.name, contact.number), 0.8), contact))
        rows.sort(key=lambda item: (-item[0], sort_name_key(item[1].name), item[1].compact()))
        return rows

    def search_recents(self, query: str) -> list[tuple[float, Recent, Contact | None]]:
        q = query.strip()
        rows: list[tuple[float, Recent, Contact | None]] = []
        for recent in self.recents:
            contact = self.lookup_number(recent.number)
            name = contact.name if contact else ""
            score = fuzzy_score(q, name, recent.number)
            if score >= SEARCH_FLOOR:
                rows.append((score, recent, contact))
        if q:
            rows.sort(key=lambda item: (-item[0], -item[1].count))
        return rows

    def record_sent(self, numbers: list[str], when: datetime | None = None) -> None:
        stamp = (when or datetime.now()).isoformat(timespec="seconds")
        by_num = {item.compact(): item for item in self.recents}
        batch: list[str] = []
        for raw in numbers:
            try:
                parsed = parse_numbers(raw)[0]
            except (ValueError, IndexError):
                parsed = compact_number(raw)
            if not parsed:
                continue
            key = compact_number(parsed)
            prev = by_num.get(key)
            by_num[key] = Recent(number=parsed, used_at=stamp, count=(prev.count + 1 if prev else 1))
            batch.append(key)
        ordered: list[Recent] = []
        seen: set[str] = set()
        for key in batch:
            if key in seen:
                continue
            ordered.append(by_num[key])
            seen.add(key)
        for item in self.recents:
            key = item.compact()
            if key in seen:
                continue
            ordered.append(by_num.get(key, item))
            seen.add(key)
        self.recents = ordered[:RECENT_LIMIT]

    def save_named(self, number: str, name: str, favorite: bool | None = None) -> Contact:
        parsed = parse_numbers(number)
        if not parsed:
            raise ValueError("Keine gültige Faxnummer")
        compact = parsed[0]
        title = name.strip()
        if not title:
            raise ValueError("Bitte einen Namen angeben")
        if not NUMBER_CHARS.fullmatch(compact):
            raise ValueError(f"Ungültige Nummer: {number!r}")
        existing = next((c for c in self.contacts if c.compact() == compact), None)
        if existing is not None:
            fav = existing.favorite if favorite is None else bool(favorite)
            updated = replace(existing, name=title, number=compact, favorite=fav)
            self.contacts = [updated if c.id == existing.id else c for c in self.contacts]
            return updated
        contact = Contact(
            id=uuid.uuid4().hex[:12],
            name=title,
            number=compact,
            favorite=bool(favorite),
            source="local",
        )
        self.contacts.append(contact)
        return contact

    def set_favorite(self, contact_id: str, favorite: bool) -> Contact | None:
        for i, contact in enumerate(self.contacts):
            if contact.id == contact_id:
                updated = replace(contact, favorite=bool(favorite))
                self.contacts[i] = updated
                return updated
        return None

    def delete_local(self, contact_id: str) -> bool:
        before = len(self.contacts)
        self.contacts = [c for c in self.contacts if c.id != contact_id]
        return len(self.contacts) < before

    def source_notes(self) -> list[str]:
        notes: list[str] = []
        if self.mode() == MODE_LOCAL:
            notes.append("Lokales Adressbuch aktiv.")
            return notes
        notes.append("Verzeichnismodus (LDAP/vCard / NovaMail) aktiv.")
        any_enabled = False
        for src in self.extra_sources:
            if not getattr(src, "enabled", False):
                continue
            any_enabled = True
            err = getattr(src, "_error", "") or ""
            cache = getattr(src, "_cache", None)
            if cache is None and not err:
                notes.append(f"{src.label}: wird geladen …")
                continue
            if err:
                notes.append(f"{src.label}: Fehler — {err}")
            else:
                notes.append(f"{src.label}: {len(cache or [])} Einträge")
        if not any_enabled:
            notes.append("Kein LDAP/vCard aktiv — in den Einstellungen NovaMail/LDAP konfigurieren.")
        return notes


def _contact_from_dict(raw: dict[str, Any]) -> Contact | None:
    number = compact_number(str(raw.get("number") or ""))
    name = str(raw.get("name") or "").strip()
    if not number:
        return None
    cid = str(raw.get("id") or uuid.uuid4().hex[:12])
    return Contact(
        id=cid,
        name=name,
        number=number,
        favorite=bool(raw.get("favorite")),
        source=str(raw.get("source") or "local"),
        notes=str(raw.get("notes") or ""),
    )


def _recent_from_dict(raw: dict[str, Any]) -> Recent | None:
    number = compact_number(str(raw.get("number") or ""))
    if not number:
        return None
    try:
        count = max(1, int(raw.get("count") or 1))
    except (TypeError, ValueError):
        count = 1
    return Recent(number=number, used_at=str(raw.get("used_at") or ""), count=count)


def _merge_source_config(raw: object) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_SOURCES))
    if not isinstance(raw, dict):
        return merged
    if "mode" in raw:
        merged["mode"] = addressbook_mode({"mode": raw.get("mode")})
    for key in ("ldap", "vcard"):
        extra = raw.get(key)
        if isinstance(extra, dict):
            merged[key].update(extra)
    return merged


def save_sources_config(sources: dict[str, Any], path: Path | None = None) -> AddressBook:
    """Update only the sources section of phonebook.json and reload."""
    book = load_book(path)
    book.sources_config = _merge_source_config(sources)
    book.extra_sources = build_extra_sources(book.sources_config)
    save_book(book, path)
    return book



def empty_book() -> AddressBook:
    cfg = json.loads(json.dumps(DEFAULT_SOURCES))
    return AddressBook(sources_config=cfg, extra_sources=build_extra_sources(cfg))


def load_book(path: Path | None = None) -> AddressBook:
    book_path = path or phonebook_path()
    data: dict[str, Any] = {}
    if book_path.exists():
        try:
            loaded = json.loads(book_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    contacts: list[Contact] = []
    seen_ids: set[str] = set()
    for item in data.get("contacts") or []:
        if not isinstance(item, dict):
            continue
        contact = _contact_from_dict(item)
        if contact is None or contact.id in seen_ids:
            continue
        seen_ids.add(contact.id)
        contacts.append(contact)
    recents: list[Recent] = []
    seen_nums: set[str] = set()
    for item in data.get("recents") or []:
        if not isinstance(item, dict):
            continue
        recent = _recent_from_dict(item)
        if recent is None or recent.compact() in seen_nums:
            continue
        seen_nums.add(recent.compact())
        recents.append(recent)
    sources_config = _merge_source_config(data.get("sources"))
    if not recents:
        for number in load_settings().last_numbers:
            key = compact_number(number)
            if not key or key in seen_nums:
                continue
            recents.append(Recent(number=key, used_at="", count=1))
            seen_nums.add(key)
    return AddressBook(
        contacts=contacts,
        recents=recents[:RECENT_LIMIT],
        sources_config=sources_config,
        extra_sources=build_extra_sources(sources_config),
    )


def save_book(book: AddressBook, path: Path | None = None) -> None:
    book_path = path or phonebook_path()
    book_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "contacts": [asdict(c) for c in book.contacts],
        "recents": [asdict(r) for r in book.recents],
        "sources": book.sources_config,
    }
    book_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
