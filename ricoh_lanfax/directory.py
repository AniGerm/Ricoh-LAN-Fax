"""LDAP and vCard/CardDAV directory backends (NovaMail-compatible defaults)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

# NovaMail embedded directory (ADR 0007 / ldap_server.rs)
NOVAMAIL_LDAP_PORT = 1389
NOVAMAIL_CARDDAV_PORT = 8765
NOVAMAIL_BASE_DN = "ou=people,dc=novamail"
NOVAMAIL_BIND_DN = "cn=novamail,dc=novamail"
NOVAMAIL_USERNAME = "novamail"
NOVAMAIL_LDAP_FILTER = "(objectClass=inetOrgPerson)"
NOVAMAIL_NAME_ATTR = "cn"
NOVAMAIL_NUMBER_ATTRS = ["facsimileTelephoneNumber", "telephoneNumber", "fax", "mobile"]
NOVAMAIL_CARDDAV_PATH = "/addressbooks/novamail/"


def novamail_defaults(host: str, password: str = "", username: str = NOVAMAIL_USERNAME) -> dict[str, Any]:
    """Fill LDAP + CardDAV settings for a NovaMail hub PC in Server mode."""
    host = host.strip()
    user = (username or NOVAMAIL_USERNAME).strip() or NOVAMAIL_USERNAME
    bind_dn = NOVAMAIL_BIND_DN if user == NOVAMAIL_USERNAME else f"cn={user},dc=novamail"
    return {
        "mode": "directory",
        "ldap": {
            "enabled": True,
            "url": f"ldap://{host}:{NOVAMAIL_LDAP_PORT}",
            "bind_dn": bind_dn,
            "bind_password": password,
            "base_dn": NOVAMAIL_BASE_DN,
            "filter": NOVAMAIL_LDAP_FILTER,
            "name_attr": NOVAMAIL_NAME_ATTR,
            "number_attrs": list(NOVAMAIL_NUMBER_ATTRS),
            "preset": "novamail",
        },
        "vcard": {
            "enabled": False,
            "path": f"http://{host}:{NOVAMAIL_CARDDAV_PORT}{NOVAMAIL_CARDDAV_PATH}",
            "username": user,
            "password": password,
        },
    }


def _attr_values(entry_attrs: dict[str, Any], name: str) -> list[str]:
    for key, value in entry_attrs.items():
        if key.lower() == name.lower():
            if value is None:
                return []
            if isinstance(value, (list, tuple)):
                return [str(v).strip() for v in value if str(v).strip()]
            text = str(value).strip()
            return [text] if text else []
    return []


def _first_attr(entry_attrs: dict[str, Any], *names: str) -> str:
    for name in names:
        values = _attr_values(entry_attrs, name)
        if values:
            return values[0]
    return ""


def _pick_fax_numbers(entry_attrs: dict[str, Any], number_attrs: list[str]) -> list[str]:
    """Prefer fax attributes, then voice/mobile. Deduplicate compact forms."""
    from .phonebook import compact_number, digits_only

    fax_names = {"facsimiletelephonenumber", "fax"}
    ordered: list[str] = []
    seen: set[str] = set()

    def add_from(names: list[str]) -> None:
        for name in names:
            for raw in _attr_values(entry_attrs, name):
                compact = compact_number(raw)
                if not compact or not digits_only(compact):
                    continue
                if compact in seen:
                    continue
                seen.add(compact)
                ordered.append(compact)

    preferred = [a for a in number_attrs if a.lower() in fax_names]
    fallback = [a for a in number_attrs if a.lower() not in fax_names]
    if not preferred and not fallback:
        preferred = ["facsimileTelephoneNumber"]
        fallback = ["telephoneNumber", "mobile"]
    add_from(preferred)
    if not ordered:
        add_from(fallback)
    return ordered


def fetch_ldap_contacts(
    *,
    url: str,
    base_dn: str,
    bind_dn: str = "",
    bind_password: str = "",
    search_filter: str = NOVAMAIL_LDAP_FILTER,
    name_attr: str = NOVAMAIL_NAME_ATTR,
    number_attrs: list[str] | None = None,
) -> list:
    """Search an LDAP directory and map inetOrgPerson entries to fax contacts."""
    from .phonebook import Contact

    try:
        from ldap3 import ALL, SUBTREE, Connection, Server
        from ldap3.utils.uri import parse_uri
    except ImportError as exc:
        raise RuntimeError(
            "python3-ldap3 fehlt. sudo apt install python3-ldap3"
        ) from exc

    uri = (url or "").strip()
    if not uri:
        raise ValueError("LDAP-URL fehlt")
    base = (base_dn or "").strip()
    if not base:
        raise ValueError("LDAP base DN fehlt")
    attrs = list(number_attrs or NOVAMAIL_NUMBER_ATTRS)
    want = list(
        dict.fromkeys(
            [
                name_attr,
                "cn",
                "displayName",
                "givenName",
                "sn",
                *attrs,
            ]
        )
    )

    parsed = parse_uri(uri)
    host = parsed.get("host") or ""
    port = parsed.get("port")
    use_ssl = bool(parsed.get("ssl"))
    if not host:
        raise ValueError(f"Ungültige LDAP-URL: {uri}")
    server = Server(host, port=port, use_ssl=use_ssl, get_info=ALL)
    user = (bind_dn or "").strip() or None
    password = bind_password or ""
    conn = Connection(server, user=user, password=password, auto_bind=True, receive_timeout=12)
    try:
        ok = conn.search(
            search_base=base,
            search_filter=search_filter or NOVAMAIL_LDAP_FILTER,
            search_scope=SUBTREE,
            attributes=want,
        )
        if not ok and conn.result.get("result") not in (0, None):
            raise RuntimeError(f"LDAP-Suche fehlgeschlagen: {conn.result}")
        contacts = []
        for entry in conn.entries:
            raw_attrs = entry.entry_attributes_as_dict
            dn = str(entry.entry_dn)
            name = _first_attr(raw_attrs, name_attr, "displayName", "cn")
            if not name:
                given = _first_attr(raw_attrs, "givenName")
                family = _first_attr(raw_attrs, "sn")
                name = f"{given} {family}".strip() or dn
            numbers = _pick_fax_numbers(raw_attrs, attrs)
            for index, number in enumerate(numbers):
                suffix = "" if index == 0 else f" #{index + 1}"
                contacts.append(
                    Contact(
                        id=f"ldap-{uuid.uuid5(uuid.NAMESPACE_URL, f'{dn}|{number}').hex[:12]}",
                        name=f"{name}{suffix}",
                        number=number,
                        favorite=False,
                        source="ldap",
                        notes=dn,
                    )
                )
        return contacts
    finally:
        try:
            conn.unbind()
        except Exception:  # noqa: BLE001
            pass


_TEL_RE = re.compile(
    r"^TEL(?:;([^:]*))?:(.+)$",
    re.IGNORECASE,
)
_FN_RE = re.compile(r"^FN(?:;[^:]*)?:(.+)$", re.IGNORECASE)
_N_RE = re.compile(r"^N(?:;[^:]*)?:(.+)$", re.IGNORECASE)
_UID_RE = re.compile(r"^UID(?:;[^:]*)?:(.+)$", re.IGNORECASE)


def _unfold_vcard(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw:
            continue
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def parse_vcard_text(text: str) -> list:
    """Parse vCard 3.0 text (NovaMail / RFC style) into fax contacts."""
    contacts = []
    current: list[str] = []
    for line in _unfold_vcard(text):
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            current = [line]
            continue
        if not current:
            continue
        current.append(line)
        if upper.startswith("END:VCARD"):
            contact = _vcard_block_to_contact(current)
            if contact is not None:
                contacts.append(contact)
            current = []
    return contacts


def _vcard_block_to_contact(lines: list[str]):
    from .phonebook import Contact, compact_number, digits_only

    fn = ""
    family = ""
    given = ""
    uid = ""
    faxes: list[str] = []
    voices: list[str] = []
    for line in lines:
        m_fn = _FN_RE.match(line)
        if m_fn:
            fn = m_fn.group(1).strip()
            continue
        m_n = _N_RE.match(line)
        if m_n:
            parts = m_n.group(1).split(";")
            family = (parts[0] if parts else "").strip()
            given = (parts[1] if len(parts) > 1 else "").strip()
            continue
        m_uid = _UID_RE.match(line)
        if m_uid:
            uid = m_uid.group(1).strip()
            continue
        m_tel = _TEL_RE.match(line)
        if not m_tel:
            continue
        params = (m_tel.group(1) or "").upper()
        number = compact_number(m_tel.group(2))
        if not number or not digits_only(number):
            continue
        if "FAX" in params:
            faxes.append(number)
        else:
            voices.append(number)
    numbers = faxes or voices
    if not numbers:
        return None
    name = fn or f"{given} {family}".strip() or numbers[0]
    number = numbers[0]
    cid = uid or uuid.uuid5(uuid.NAMESPACE_URL, f"{name}|{number}").hex
    return Contact(
        id=f"vcard-{cid[:12]}",
        name=name,
        number=number,
        favorite=False,
        source="vcard",
        notes="",
    )


def _basic_auth_header(username: str, password: str) -> str:
    import base64

    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _http_request(
    url: str,
    *,
    method: str = "GET",
    username: str = "",
    password: str = "",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 12.0,
) -> tuple[int, str, bytes]:
    hdrs = dict(headers or {})
    if username or password:
        hdrs["Authorization"] = _basic_auth_header(username, password)
    req = urlrequest.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            return int(resp.status), str(resp.headers.get("Content-Type") or ""), data
    except urlerror.HTTPError as exc:
        data = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), str(exc.headers.get("Content-Type") if exc.headers else ""), data


def _carddav_hrefs(collection_url: str, xml_body: bytes) -> list[str]:
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError:
        return []
    hrefs: list[str] = []
    for el in root.iter():
        if el.tag.lower().endswith("href") and el.text:
            href = el.text.strip()
            if href.lower().endswith(".vcf"):
                hrefs.append(urljoin(collection_url, href))
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for href in hrefs:
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def fetch_vcard_contacts(
    path_or_url: str,
    *,
    username: str = "",
    password: str = "",
) -> list:
    """Load contacts from a .vcf file, HTTP vCard URL, or NovaMail CardDAV collection."""
    target = (path_or_url or "").strip()
    if not target:
        raise ValueError("vCard-Pfad oder CardDAV-URL fehlt")

    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"}:
        # Collection URL (NovaMail CardDAV) → PROPFIND then GET each .vcf
        if target.endswith("/") or "addressbooks" in parsed.path:
            collection = target if target.endswith("/") else target + "/"
            propfind = (
                b'<?xml version="1.0" encoding="utf-8"?>'
                b'<d:propfind xmlns:d="DAV:">'
                b"<d:prop><d:getetag/><d:resourcetype/></d:prop>"
                b"</d:propfind>"
            )
            status, _ctype, body = _http_request(
                collection,
                method="PROPFIND",
                username=username,
                password=password,
                headers={
                    "Depth": "1",
                    "Content-Type": "application/xml; charset=utf-8",
                },
                body=propfind,
            )
            if status not in {207, 200}:
                raise RuntimeError(f"CardDAV PROPFIND fehlgeschlagen (HTTP {status})")
            contacts = []
            for href in _carddav_hrefs(collection, body):
                st, ctype, raw = _http_request(href, username=username, password=password)
                if st != 200:
                    continue
                text = raw.decode("utf-8", errors="replace")
                if "vcard" in ctype.lower() or "BEGIN:VCARD" in text.upper():
                    contacts.extend(parse_vcard_text(text))
            return contacts

        status, _ctype, body = _http_request(target, username=username, password=password)
        if status != 200:
            raise RuntimeError(f"vCard-Download fehlgeschlagen (HTTP {status})")
        return parse_vcard_text(body.decode("utf-8", errors="replace"))

    path = Path(target).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"vCard-Datei nicht gefunden: {path}")
    return parse_vcard_text(path.read_text(encoding="utf-8", errors="replace"))


def probe_ldap(
    *,
    url: str,
    base_dn: str,
    bind_dn: str = "",
    bind_password: str = "",
    search_filter: str = NOVAMAIL_LDAP_FILTER,
) -> str:
    contacts = fetch_ldap_contacts(
        url=url,
        base_dn=base_dn,
        bind_dn=bind_dn,
        bind_password=bind_password,
        search_filter=search_filter,
    )
    return f"LDAP OK — {len(contacts)} Einträge mit Nummer"


def probe_vcard(path_or_url: str, *, username: str = "", password: str = "") -> str:
    contacts = fetch_vcard_contacts(path_or_url, username=username, password=password)
    return f"vCard/CardDAV OK — {len(contacts)} Einträge"


@dataclass
class LdapSource:
    source_id: str = "ldap"
    label: str = "LDAP"
    writable: bool = False
    enabled: bool = False
    url: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    base_dn: str = ""
    filter: str = NOVAMAIL_LDAP_FILTER
    name_attr: str = NOVAMAIL_NAME_ATTR
    number_attrs: list[str] = field(default_factory=lambda: list(NOVAMAIL_NUMBER_ATTRS))
    _cache: list | None = field(default=None, repr=False, compare=False)
    _error: str = field(default="", repr=False, compare=False)

    def list_contacts(self) -> list:
        if not self.enabled:
            return []
        if self._cache is not None:
            return list(self._cache)
        try:
            self._cache = fetch_ldap_contacts(
                url=self.url,
                base_dn=self.base_dn,
                bind_dn=self.bind_dn,
                bind_password=self.bind_password,
                search_filter=self.filter,
                name_attr=self.name_attr,
                number_attrs=self.number_attrs,
            )
            self._error = ""
        except Exception as exc:  # noqa: BLE001
            self._cache = []
            self._error = str(exc)
        return list(self._cache)

    def search(self, query: str) -> list:
        from .phonebook import fuzzy_score

        q = query.strip()
        rows = self.list_contacts()
        if not q:
            return rows
        scored = [(fuzzy_score(q, c.name, c.number), c) for c in rows]
        scored = [(s, c) for s, c in scored if s >= 0.42]
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [c for _s, c in scored]

    def last_error(self) -> str:
        return self._error


@dataclass
class VcardSource:
    source_id: str = "vcard"
    label: str = "vCard"
    writable: bool = False
    enabled: bool = False
    path: str = ""
    username: str = ""
    password: str = ""
    _cache: list | None = field(default=None, repr=False, compare=False)
    _error: str = field(default="", repr=False, compare=False)

    def list_contacts(self) -> list:
        if not self.enabled:
            return []
        if self._cache is not None:
            return list(self._cache)
        try:
            self._cache = fetch_vcard_contacts(
                self.path, username=self.username, password=self.password
            )
            self._error = ""
        except Exception as exc:  # noqa: BLE001
            self._cache = []
            self._error = str(exc)
        return list(self._cache)

    def search(self, query: str) -> list:
        from .phonebook import fuzzy_score

        q = query.strip()
        rows = self.list_contacts()
        if not q:
            return rows
        scored = [(fuzzy_score(q, c.name, c.number), c) for c in rows]
        scored = [(s, c) for s, c in scored if s >= 0.42]
        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [c for _s, c in scored]

    def last_error(self) -> str:
        return self._error

