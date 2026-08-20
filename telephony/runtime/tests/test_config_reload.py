from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telephony.runtime.config_reload import (
    read_runtime_config_reload_token,
    runtime_config_reload_path,
    write_runtime_config_reload_token,
)


class RuntimeConfigReloadTest(unittest.TestCase):
    def test_marker_round_trip_is_atomic_site_local_and_changes_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            bench = Path(temporary)
            path = runtime_config_reload_path(site="client.localhost", bench_path=bench)
            self.assertEqual(
                path,
                bench / "sites" / "client.localhost" / "private" / "telephony" / "runtime-config.reload",
            )
            self.assertEqual(
                read_runtime_config_reload_token(site="client.localhost", bench_path=bench), ""
            )
            first = write_runtime_config_reload_token(path)
            second = write_runtime_config_reload_token(path)
            self.assertNotEqual(first, second)
            self.assertEqual(
                read_runtime_config_reload_token(site="client.localhost", bench_path=bench), second
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
