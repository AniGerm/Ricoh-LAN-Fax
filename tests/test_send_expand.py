from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.expand_strings import expand_and_scan, extract_strings
from ricoh_lanfax.send import build_job, wrap_pjl_rfax


class ExpandTests(unittest.TestCase):
    def test_missing_disk1_explains_what_is_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            disk1 = Path(tmp) / "DISK1"
            disk1.mkdir()
            out = Path(tmp) / "out"
            report = expand_and_scan(disk1, out)
            self.assertIn("No compressed driver files", report)
            self.assertIn("rictW0ge.dl_", report)

    def test_szdd_expands_pe_when_disk1_present(self) -> None:
        src = ROOT / "DISK1" / "rictW0cj.dl_"
        if not src.exists():
            self.skipTest("DISK1 binaries not present")
        from ricoh_lanfax.expand_strings import decompress_szdd

        pe = decompress_szdd(src.read_bytes())
        self.assertTrue(pe.startswith(b"MZ"))
        self.assertGreater(len(pe), 1000)
        data = b"xxxx @PJL ENTER LANGUAGE=RFAX yyyy FAXNUMBER=0123 " + (b"\x00" * 8)
        hits = extract_strings(data)
        self.assertTrue(any("RFAX" in h for h in hits))


class SendTests(unittest.TestCase):
    def test_template_replaces_number(self) -> None:
        raw = wrap_pjl_rfax(b"II*\x00", "1111111")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.raw"
            path.write_bytes(raw)
            dummy = Path(tmp) / "page.tif"
            dummy.write_bytes(b"II*\x00")
            job, note = build_job(dummy, "9999999", template=path, replace_from="1111111")
            self.assertIn("DEST_ADDRESS", note)
            self.assertIn(b"9999999", job)
            self.assertNotIn(b"1111111", job)

    def test_cups_banner_becomes_g4_page(self) -> None:
        from ricoh_lanfax.send import FAX_HEIGHT_PX, is_cups_banner, raster_to_g4_pages

        banner = (
            b"#PDF-BANNER\nTemplate default-testpage.pdf\n"
            b"Show printer-name printer-info\n"
        )
        self.assertTrue(is_cups_banner(banner))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.pdf"
            path.write_bytes(banner)
            pages = raster_to_g4_pages(path)
            self.assertGreaterEqual(len(pages), 1)
            self.assertEqual(pages[0][0], FAX_HEIGHT_PX)
            self.assertGreater(len(pages[0][1]), 20)

    def test_wrap_pages_matches_ltsc_page_records(self) -> None:
        from ricoh_lanfax.rfax import dest_checksum, parse_records
        from ricoh_lanfax.send import wrap_pages

        fake = b"\x00" * 80
        job = wrap_pages([(2259, fake), (2259, fake)], "223344", hostname="H", loginname="U")
        recs, rest = parse_records(job[job.find(b"\x1b\x01\x01") : job.rfind(b"\x1b%-12345X")])
        self.assertEqual(rest, b"")
        names = [r.name for r in recs]
        self.assertNotIn("END_PHYSICAL_PAGE", names)
        self.assertEqual(names.count("IMAGE_PAGE"), 2)
        dest = next(r for r in recs if r.name == "DEST_ADDRESS")
        self.assertEqual(dest.payload, b"\x00223344")
        csum = next(r for r in recs if r.name == "DEST_CHECKSUM")
        self.assertEqual(csum.payload[0] | (csum.payload[1] << 8), dest_checksum(0, "223344"))

    def test_cover_ps_has_number_and_message(self) -> None:
        from ricoh_lanfax.cover import CoverSpec, build_cover_ps, raster_cover_g4
        from ricoh_lanfax.send import FAX_HEIGHT_PX

        spec = CoverSpec(
            numbers=["223344"],
            sender="Max",
            hostname="testhost",
            title="Testdokument",
            message="Bitte (sofort) anrufen.",
            show_info=True,
            document_pages=4,
            date_s="2026/09/17",
            time_s="09:00:00",
        )
        ps = build_cover_ps(spec)
        self.assertIn(b"223344", ps)
        self.assertIn(b"Bitte \\(sofort\\) anrufen.", ps)
        self.assertIn(b"Max", ps)
        self.assertIn(b"17.09.2026", ps)
        self.assertIn(b"Courier-Bold", ps)
        self.assertIn(b"rlineto stroke", ps)
        self.assertNotIn(b"Host:", ps)
        self.assertNotIn(b"testhost", ps)
        self.assertNotIn(b"Testdokument", ps)
        self.assertNotIn(b"Datei:", ps)
        pages = raster_cover_g4(spec)
        self.assertEqual(pages[0], FAX_HEIGHT_PX)
        self.assertGreater(len(pages[1]), 20)

    def test_cover_without_details_still_has_datetime(self) -> None:
        from ricoh_lanfax.cover import CoverSpec, build_cover_ps

        spec = CoverSpec(
            numbers=["223344"],
            sender="Max",
            hostname="",
            title="",
            message="",
            show_info=False,
            date_s="17.09.2026",
            time_s="10:33",
        )
        ps = build_cover_ps(spec)
        self.assertIn(b"FAX", ps)
        self.assertIn(b"17.09.2026", ps)
        self.assertIn(b"10:33", ps)
        self.assertNotIn(b"An:", ps)
        self.assertNotIn(b"Von:", ps)
        self.assertNotIn(b"Seiten:", ps)
        self.assertNotIn(b"223344", ps)


if __name__ == "__main__":
    unittest.main()
