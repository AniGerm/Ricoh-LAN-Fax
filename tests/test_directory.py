from pathlib import Path
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.directory import (
    NOVAMAIL_BASE_DN,
    NOVAMAIL_BIND_DN,
    novamail_defaults,
    parse_vcard_text,
    fetch_vcard_contacts,
)
from ricoh_lanfax.phonebook import (
    MODE_DIRECTORY,
    MODE_LOCAL,
    addressbook_mode,
    build_extra_sources,
    load_book,
    save_book,
    AddressBook,
)


SAMPLE_VCARD = """BEGIN:VCARD
VERSION:3.0
UID:11111111-1111-1111-1111-111111111111
FN:Fax Test
N:Test;Fax;;;
TEL;TYPE=VOICE:030111
TEL;TYPE=FAX:030999888
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Only Voice
TEL;TYPE=VOICE:040123
END:VCARD
"""


class NovaMailDefaultsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = novamail_defaults("10.0.0.5", password="secret")
        self.assertEqual(cfg["mode"], MODE_DIRECTORY)
        self.assertEqual(cfg["ldap"]["url"], "ldap://10.0.0.5:1389")
        self.assertEqual(cfg["ldap"]["base_dn"], NOVAMAIL_BASE_DN)
        self.assertEqual(cfg["ldap"]["bind_dn"], NOVAMAIL_BIND_DN)
        self.assertTrue(cfg["ldap"]["enabled"])
        self.assertIn("addressbooks/novamail", cfg["vcard"]["path"])


class VcardParseTests(unittest.TestCase):
    def test_prefers_fax(self) -> None:
        contacts = parse_vcard_text(SAMPLE_VCARD)
        self.assertEqual(len(contacts), 2)
        by_name = {c.name: c for c in contacts}
        self.assertEqual(by_name["Fax Test"].number, "030999888")
        self.assertEqual(by_name["Only Voice"].number, "040123")
        self.assertEqual(by_name["Fax Test"].source, "vcard")

    def test_file_and_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.vcf"
            path.write_text(SAMPLE_VCARD, encoding="utf-8")
            from_file = fetch_vcard_contacts(str(path))
            self.assertEqual(len(from_file), 2)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                data = SAMPLE_VCARD.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/vcard")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            rows = fetch_vcard_contacts(f"http://127.0.0.1:{port}/contacts.vcf")
            self.assertEqual(len(rows), 2)
        finally:
            server.shutdown()
            server.server_close()



class ModeTests(unittest.TestCase):
    def test_local_ignores_enabled_flags(self) -> None:
        cfg = {
            "mode": MODE_LOCAL,
            "ldap": {"enabled": True, "url": "ldap://example.invalid:1389", "base_dn": "ou=people,dc=novamail"},
            "vcard": {"enabled": True, "path": "/tmp/none.vcf"},
        }
        extras = build_extra_sources(cfg)
        self.assertFalse(extras[0].enabled)
        self.assertFalse(extras[1].enabled)
        self.assertEqual(addressbook_mode(cfg), MODE_LOCAL)

    def test_directory_enables_sources(self) -> None:
        cfg = {
            "mode": MODE_DIRECTORY,
            "ldap": {"enabled": True, "url": "ldap://127.0.0.1:1389", "base_dn": NOVAMAIL_BASE_DN},
            "vcard": {"enabled": False, "path": ""},
        }
        extras = build_extra_sources(cfg)
        self.assertTrue(extras[0].enabled)
        self.assertFalse(extras[1].enabled)

    def test_roundtrip_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phonebook.json"
            book = AddressBook()
            book.sources_config = novamail_defaults("192.168.1.20", password="x")
            book.extra_sources = build_extra_sources(book.sources_config)
            save_book(book, path)
            loaded = load_book(path)
            self.assertEqual(loaded.mode(), MODE_DIRECTORY)
            self.assertEqual(loaded.sources_config["ldap"]["bind_dn"], NOVAMAIL_BIND_DN)
            self.assertEqual(loaded.sources_config["ldap"]["bind_password"], "x")


if __name__ == "__main__":
    unittest.main()
