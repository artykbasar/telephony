import os
import unittest
from pathlib import Path

from telephony.runtime.socket_path import runtime_status_path, voice_websocket_socket_path


class SocketPathTest(unittest.TestCase):
    def test_voice_socket_uses_shared_sites_volume_when_path_is_short_enough(self):
        bench = Path("/bench")
        first = voice_websocket_socket_path(site="site.test", bench_path=bench, environment={})
        second = voice_websocket_socket_path(site="site.test", bench_path=bench, environment={})
        self.assertEqual(first, second)
        self.assertEqual(first.parent, Path("/bench/sites/.telephony-runtime"))
        self.assertTrue(str(first).endswith("-voice.sock"))
        self.assertLess(len(os.fsencode(str(first))), 100)

    def test_voice_socket_falls_back_to_short_temp_path_for_long_bench_paths(self):
        bench = Path("/") / ("very-long-bench-path-" * 8)
        path = voice_websocket_socket_path(site="site.test", bench_path=bench, environment={})
        self.assertIn(f"frappe-telephony-{os.getuid()}", str(path))
        self.assertLess(len(os.fsencode(str(path))), 100)

    def test_runtime_dir_environment_override_wins(self):
        path = voice_websocket_socket_path(
            site="site.test",
            bench_path=Path("/bench"),
            environment={"TELEPHONY_RUNTIME_DIR": "/shared/telephony"},
        )
        self.assertEqual(path.parent, Path("/shared/telephony"))

    def test_runtime_status_is_site_private(self):
        path = runtime_status_path(site="site.test", bench_path=Path("/bench"))
        self.assertEqual(path, Path("/bench/sites/site.test/private/telephony/runtime-status.json"))


if __name__ == "__main__":
    unittest.main()
