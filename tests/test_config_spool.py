from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.config import Settings, load_settings, parse_numbers, save_settings
from ricoh_lanfax.cups_backend import run_backend
from ricoh_lanfax.session import session_env
from ricoh_lanfax.spool import (
    job_paths,
    pending_jobs,
    purge_stale_jobs,
    restrict_job_files,
    unlink_job_files,
    write_json,
    write_result,
)


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

    def test_json_and_result_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                paths = job_paths("7", Path(tmp))
                write_json(paths["json"], {"job_id": "7"})
                write_result("7", "ok", "done")
                self.assertEqual(paths["json"].stat().st_mode & 0o777, 0o600)
                self.assertEqual(paths["result"].stat().st_mode & 0o777, 0o600)
            finally:
                os.environ.pop("RICOH_LANFAX_SPOOL", None)

    def test_restrict_sets_600(self) -> None:
        import getpass

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                path = Path(tmp) / "job-mode.pdf"
                path.write_bytes(b"%PDF")
                path.chmod(0o666)
                restrict_job_files(getpass.getuser(), path)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                os.environ.pop("RICOH_LANFAX_SPOOL", None)

    def test_unknown_user_aborts_without_world_readable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                paths = job_paths("8", Path(tmp))
                paths["doc"].write_bytes(b"%PDF")
                paths["doc"].chmod(0o644)
                write_json(paths["json"], {"job_id": "8"})
                with self.assertRaises(RuntimeError):
                    restrict_job_files("no_such_lanfax_user_zzz", paths["doc"], paths["json"])
                self.assertEqual(paths["doc"].stat().st_mode & 0o777, 0o644)
            finally:
                os.environ.pop("RICOH_LANFAX_SPOOL", None)

    def test_purge_stale_and_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                old = Path(tmp) / "job-old.json"
                old.write_text("{}\n", encoding="utf-8")
                os.utime(old, (time.time() - 25 * 3600, time.time() - 25 * 3600))
                fresh = Path(tmp) / "job-fresh.json"
                write_json(fresh, {"job_id": "fresh"})
                purge_stale_jobs(max_age=24 * 3600)
                self.assertFalse(old.exists())
                self.assertTrue(fresh.exists())
                paths = job_paths("fresh", Path(tmp))
                write_json(paths["json"], {"job_id": "fresh"})
                paths["doc"].write_bytes(b"x")
                unlink_job_files(paths)
                self.assertFalse(paths["json"].exists())
                self.assertFalse(paths["doc"].exists())
            finally:
                os.environ.pop("RICOH_LANFAX_SPOOL", None)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "chown needs root")
    def test_restrict_chown_when_root(self) -> None:
        import pwd

        try:
            pw = pwd.getpwnam("nobody")
        except KeyError:
            self.skipTest("no nobody user")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RICOH_LANFAX_SPOOL"] = tmp
            try:
                path = Path(tmp) / "job-chown.pdf"
                path.write_bytes(b"%PDF")
                restrict_job_files("nobody", path)
                st = path.stat()
                self.assertEqual(st.st_uid, pw.pw_uid)
                self.assertEqual(st.st_gid, pw.pw_gid)
                self.assertEqual(st.st_mode & 0o777, 0o600)
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
