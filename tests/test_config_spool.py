from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.config import Settings, load_settings, parse_numbers, save_settings
from ricoh_lanfax.cups_backend import run_backend
from ricoh_lanfax.session import session_env
from ricoh_lanfax.spool import job_paths, pending_jobs, write_json, write_result


class NumberTests(unittest.TestCase):
    def test_split_lines_and_comma(self) -> None:
        self.assertEqual(parse_numbers("123123123\n0049 111\n"), ["123123123", "0049111"])
        self.assertEqual(parse_numbers("111,222;333"), ["111", "222", "333"])

    def test_rejects_letters(self) -> None:
        with self.assertRaises(ValueError):
            parse_numbers("abc")


class SettingsTests(unittest.TestCase):
    def test_cover_fields_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from ricoh_lanfax import config as cfg

            old = cfg.config_path

            def fake_path() -> Path:
                return Path(tmp) / "config.json"

            cfg.config_path = fake_path  # type: ignore[method-assign]
            try:
                save_settings(
                    Settings(
                        printer_host="printer.example.test",
                        last_numbers=["223344"],
                        sender_name="Sender",
                        cover_enabled=True,
                        cover_show_info=True,
                        cover_message="Bitte zurückrufen.",
                    )
                )
                loaded = load_settings()
                self.assertEqual(loaded.printer_host, "printer.example.test")
                self.assertEqual(loaded.last_numbers, ["223344"])
                self.assertEqual(loaded.sender_name, "Sender")
                self.assertTrue(loaded.cover_enabled)
                self.assertTrue(loaded.cover_show_info)
                self.assertEqual(loaded.cover_message, "Bitte zurückrufen.")
                self.assertEqual(loaded.preview_zoom, "fit")
            finally:
                cfg.config_path = old


class SpoolTests(unittest.TestCase):
    def test_pending_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                paths = job_paths("42", Path(tmp))
                paths["doc"].write_bytes(b"%PDF")
                write_json(paths["json"], {"job_id": "42", "title": "t", "document": str(paths["doc"])})
                jobs = pending_jobs()
                self.assertEqual(len(jobs), 1)
                write_result("42", "ok", "sent")
                self.assertEqual(pending_jobs(), [])
            finally:
                os.environ.pop("RICOH_LANFAX_SPOOL", None)


class SessionTests(unittest.TestCase):
    def test_session_env_has_home(self) -> None:
        env = session_env("max")
        self.assertIn("HOME", env)
        self.assertTrue(env["HOME"].startswith("/"))


class CupsDiscoverTests(unittest.TestCase):
    def test_lists_device(self) -> None:
        self.assertEqual(run_backend(["ricohlanfax"]), 0)


if __name__ == "__main__":
    unittest.main()
