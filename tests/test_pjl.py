from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.pjl import UEL, diff_jobs, format_report, parse_job, replace_number
from ricoh_lanfax.send import wrap_pjl_rfax


def sample_job(number: str, payload: bytes = b"II*\x00FAKE-TIFF") -> bytes:
    return (
        UEL
        + (
            "@PJL\r\n"
            '@PJL JOB NAME="test"\r\n'
            f'@PJL SET FAXNUMBER="{number}"\r\n'
            "@PJL ENTER LANGUAGE=RFAX\r\n"
        ).encode("ascii")
        + payload
        + UEL
    )


class PjlParseTests(unittest.TestCase):
    def test_splits_pjl_and_rfax_payload(self) -> None:
        raw = sample_job("0123456789")
        job = parse_job(raw)
        self.assertEqual(job.language, "RFAX")
        texts = [c.text for c in job.pjl_commands]
        self.assertTrue(any("FAXNUMBER" in t for t in texts))
        self.assertTrue(job.payload.startswith(b"II*\x00"))
        self.assertIn("0123456789", format_report(job))

    def test_diff_finds_number(self) -> None:
        a = sample_job("1111111111")
        b = sample_job("2222222222")
        report = diff_jobs(a, b, "a", "b")
        self.assertIn("1111111111", report)
        self.assertIn("2222222222", report)
        self.assertIn("differing_ranges:", report)

    def test_replace_number(self) -> None:
        raw = sample_job("1111111111")
        patched, count = replace_number(raw, "1111111111", "3333333333")
        self.assertGreaterEqual(count, 1)
        self.assertIn(b"3333333333", patched)
        self.assertNotIn(b"1111111111", patched)

    def test_wrap_roundtrip(self) -> None:
        job = wrap_pjl_rfax(b"II*\x00xx", "0049123456")
        parsed = parse_job(job)
        self.assertEqual(parsed.language, "RFAX")
        self.assertIn(b"@PJL PCFAXJOB", job)
        self.assertIn(b"0049123456", job)
        self.assertIn("DEST_ADDRESS", format_report(parsed))


if __name__ == "__main__":
    unittest.main()
