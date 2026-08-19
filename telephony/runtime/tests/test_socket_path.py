import os
import tempfile
import unittest
from pathlib import Path

from telephony.runtime.socket_path import runtime_status_path, voice_websocket_socket_path


class SocketPathTest(unittest.TestCase):
    def test_voice_socket_is_short_deterministic_and_telephony_namespaced(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            first = voice_websocket_socket_path(site="site.test", bench_path=bench, environment={})
            second = voice_websocket_socket_path(site="site.test", bench_path=bench, environment={})
            self.assertEqual(first, second)
            self.assertIn(f"frappe-telephony-{os.getuid()}", str(first))
            self.assertTrue(str(first).endswith("-voice.sock"))
            self.assertLess(len(str(first)), 100)

    def test_runtime_status_is_site_private(self):
        path = runtime_status_path(site="site.test", bench_path=Path("/bench"))
        self.assertEqual(path, Path("/bench/sites/site.test/private/telephony/runtime-status.json"))


if __name__ == "__main__": unittest.main()
