from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.pjl import format_report, parse_job
from ricoh_lanfax.rfax import dest_checksum, encode_record, parse_records
from ricoh_lanfax.send import build_job, patch_job_number, wrap_pjl_rfax


class RfaxTests(unittest.TestCase):
    def test_dest_record_roundtrip(self) -> None:
        rec = encode_record(0x01, 0x02, bytes([0]) + b"0123456789")
        records, rest = parse_records(rec)
        self.assertEqual(rest, b"")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "DEST_ADDRESS")
        self.assertIn("0123456789", records[0].describe())

    def test_checksum_matches_windows_captures(self) -> None:
        self.assertEqual(dest_checksum(0, "111111111"), 441)
        self.assertEqual(dest_checksum(0, "2222222222"), 500)
        self.assertEqual(dest_checksum(0, "123456789"), 477)

    def test_built_job_is_readable(self) -> None:
        job = wrap_pjl_rfax(b"MMR", "5551234")
        report = format_report(parse_job(job))
        self.assertIn("@PJL PCFAXJOB", report)
        self.assertIn("DEST_ADDRESS", report)
        self.assertIn("5551234", report)
        self.assertIn("IMAGE_PAGE", report)
        self.assertIn("ENTER LANGUAGE=RFAX", report)
        self.assertIn("PAGE_PRINTSIZE = A4", report)

    def test_patch_live_capture_number(self) -> None:
        caps = sorted((ROOT / "captures").glob("raw9100-*.raw"))
        if not caps:
            self.skipTest("no local captures")
        cap = caps[0]
        out = patch_job_number(cap.read_bytes(), "55555")
        report = format_report(parse_job(out))
        self.assertIn("address='55555'", report)
        self.assertIn("DEST_CHECKSUM = 265", report)
        self.assertNotIn("123456789", report)
        self.assertIn("IMAGE_PAGE", report)
        job, note = build_job(None, "99999", template=cap)
        self.assertIn("99999", format_report(parse_job(job)))
        self.assertIn("checksum", note)


if __name__ == "__main__":
    unittest.main()
