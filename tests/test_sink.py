from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ricoh_lanfax.pjl import UEL
from ricoh_lanfax.sink import JobServer, recv_until_idle


class SinkTests(unittest.TestCase):
    def test_recv_until_idle(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def client() -> None:
            time.sleep(0.05)
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.sendall(b"hello")
                time.sleep(0.05)
                sock.sendall(b" world")

        threading.Thread(target=client, daemon=True).start()
        conn, _ = server.accept()
        try:
            data = recv_until_idle(conn, 2.0, 0.4)
        finally:
            conn.close()
            server.close()
        self.assertEqual(data, b"hello world")

    def test_job_server_writes_raw_and_pjl(self) -> None:
        job = (
            UEL
            + b'@PJL SET FAXNUMBER="5551234"\r\n@PJL ENTER LANGUAGE=RFAX\r\nII*\x00DATA'
            + UEL
        )
        with tempfile.TemporaryDirectory() as tmp:
            logs: list[str] = []
            srv = JobServer("127.0.0.1", 0, Path(tmp), logs.append, idle_timeout=0.4)
            srv.start()
            self.assertTrue(srv.ready.wait(2), msg="\n".join(logs))
            self.assertIsNotNone(srv.bound_port)
            port = srv.bound_port
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                client.sendall(job)
            deadline = time.time() + 3
            raws: list[Path] = []
            while time.time() < deadline:
                raws = list(Path(tmp).glob("*.raw"))
                if raws:
                    break
                time.sleep(0.05)
            srv.stop()
            srv.join(timeout=2.0)
            self.assertTrue(raws, msg="\n".join(logs))
            saved = raws[0].read_bytes()
            self.assertEqual(saved, job)
            txts = list(Path(tmp).glob("*.pjl.txt"))
            self.assertTrue(txts)
            text = txts[0].read_text(encoding="utf-8")
            self.assertIn("RFAX", text)
            self.assertIn("5551234", text)
            pcaps = list(Path(tmp).glob("*.pcap"))
            self.assertTrue(pcaps)
            self.assertGreater(pcaps[0].stat().st_size, 24)


if __name__ == "__main__":
    unittest.main()
