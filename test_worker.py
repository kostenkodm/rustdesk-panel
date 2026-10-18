"""Status worker tests: no real network or NAS access."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import status_worker as worker


class TestStatusWorker(unittest.TestCase):
    def test_true_status_and_errors_are_not_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peers = root / "peers.json"
            now = int(time.time())
            peers.write_text(json.dumps({
                "ok": True, "stale": False, "timestamp": now,
                "devices": [{"id": "1"}, {"id": "2"}]
            }))
            with patch.object(worker, "probe_online", return_value={"1": True, "2": False}):
                result = worker.poll_once(peers, "127.0.0.1", 21115, now=now)
            self.assertEqual(result["devices"], {"1": "online", "2": "offline"})
            self.assertTrue(result["ok"])
            with patch.object(worker, "probe_online", side_effect=OSError("no route")):
                result = worker.poll_once(peers, "127.0.0.1", 21115, now=now)
            self.assertFalse(result["ok"])
            self.assertEqual(result["devices"], {})
            peers.write_text(json.dumps({
                "ok": True, "stale": True, "timestamp": now,
                "devices": [{"id": "1"}]
            }))
            with patch.object(worker, "probe_online") as probe:
                result = worker.poll_once(peers, "127.0.0.1", 21115, now=now)
                probe.assert_not_called()
            self.assertFalse(result["ok"])
            peers.write_text(json.dumps({
                "ok": True, "timestamp": now - 1000,
                "devices": [{"id": "1"}]
            }))
            result = worker.poll_once(peers, "127.0.0.1", 21115, now=now)
            self.assertFalse(result["ok"])

    def test_status_snapshot_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "statuses.json"
            worker.write_atomic(out, {"ok": True, "devices": {"x": "online"}})
            self.assertEqual(json.loads(out.read_text())["devices"]["x"], "online")
            self.assertEqual(out.stat().st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main(verbosity=2)
