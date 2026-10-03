from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.phonebook import (
    AddressBook,
    Contact,
    Recent,
    DEFAULT_SOURCES,
    build_extra_sources,
    compact_number,
    fold_text,
    fuzzy_score,
    load_book,
    save_book,
)


class FoldTests(unittest.TestCase):
    def test_umlaut_and_eszett(self) -> None:
        self.assertEqual(fold_text("Müller"), "mueller")
        self.assertEqual(fold_text("Straße"), "strasse")
        self.assertEqual(compact_number("555 123 4567"), "5551234567")


class FuzzyTests(unittest.TestCase):
    def test_name_typo_and_prefix(self) -> None:
        self.assertGreaterEqual(fuzzy_score("muler", "Müller", "030111"), 0.7)
        self.assertGreaterEqual(fuzzy_score("schmi", "Schmidt, Berta", "030222"), 0.9)

    def test_number_digits(self) -> None:
        self.assertGreaterEqual(fuzzy_score("555123", "Office", "555 1234567"), 0.9)
        self.assertLess(fuzzy_score("xyz", "Anna", "030111"), 0.42)


class BookTests(unittest.TestCase):
    def test_save_named_and_favorites_sorted(self) -> None:
        book = AddressBook()
        book.save_named("030 999", "Schmidt", favorite=False)
        book.save_named("555 11", "Clinic", favorite=True)
        book.save_named("030999", "Schmidt Büro", favorite=True)
        names = [c.name for c in book.named_sorted()]
        self.assertEqual(names[0], "Clinic")
        self.assertEqual(len(book.contacts), 2)
        self.assertEqual(book.lookup_number("030999").name, "Schmidt Büro")
        favs = [c.name for c in book.favorites()]
        self.assertEqual(favs, ["Clinic", "Schmidt Büro"])

    def test_search_orders_by_score(self) -> None:
        book = AddressBook(
            contacts=[
                Contact(id="1", name="Müller, Anna", number="5552111"),
                Contact(id="2", name="Meier, Otto", number="5552222"),
                Contact(id="3", name="Schulz", number="030333"),
            ]
        )
        hits = book.search("muler")
        self.assertTrue(hits)
        self.assertEqual(hits[0][1].name, "Müller, Anna")

    def test_recents_and_save_from_recent(self) -> None:
        book = AddressBook()
        book.record_sent(["223344", "555 11"], when=datetime(2026, 9, 17, 9, 0, 0))
        book.record_sent(["223344"], when=datetime(2026, 9, 17, 10, 0, 0))
        self.assertEqual(book.recents[0].number, "223344")
        self.assertEqual(book.recents[0].count, 2)
        self.assertEqual(book.recents[1].number, "55511")
        contact = book.save_named("223344", "Labor")
        self.assertTrue(contact.id)
        _score, recent, linked = book.search_recents("lab")[0]
        self.assertEqual(recent.number, "223344")
        self.assertIsNotNone(linked)
        self.assertEqual(linked.name, "Labor")

    def test_ldap_vcard_disabled_in_local_mode(self) -> None:
        cfg = {
            "mode": "local",
            "ldap": {**DEFAULT_SOURCES["ldap"], "enabled": True, "url": "ldap://example.invalid"},
            "vcard": {**DEFAULT_SOURCES["vcard"], "enabled": True, "path": "/tmp/none.vcf"},
        }
        extra = build_extra_sources(cfg)
        self.assertEqual(len(extra), 2)
        self.assertEqual(extra[0].source_id, "ldap")
        self.assertEqual(extra[1].source_id, "vcard")
        self.assertFalse(extra[0].enabled)
        self.assertFalse(extra[1].enabled)
        self.assertEqual(extra[0].list_contacts(), [])
        self.assertEqual(extra[1].search("x"), [])

    def test_roundtrip_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phonebook.json"
            book = AddressBook()
            book.save_named("111", "Test", favorite=True)
            book.record_sent(["111", "222"])
            save_book(book, path)
            loaded = load_book(path)
            self.assertEqual(len(loaded.contacts), 1)
            self.assertEqual(loaded.contacts[0].name, "Test")
            self.assertEqual([r.number for r in loaded.recents], ["111", "222"])
            self.assertIn("ldap", loaded.sources_config)
            self.assertFalse(loaded.sources_config["ldap"]["enabled"])


if __name__ == "__main__":
    unittest.main()
