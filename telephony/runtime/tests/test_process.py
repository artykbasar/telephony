from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telephony.runtime.process import TelephonyRuntimeProcess


class RuntimeProcessAccountReconcileTest(unittest.TestCase):
    def _process(self, bench: Path) -> TelephonyRuntimeProcess:
        process = TelephonyRuntimeProcess(
            site="client.localhost", bench_path=bench, install_signal_handlers=False
        )
        process.runtime = Mock()
        process.runtime.reconcile_accounts.return_value = {
            "added": (), "changed": ("agent-a",), "removed": (),
        }
        return process

    def test_reload_marker_reconciles_immediately(self):
        with tempfile.TemporaryDirectory() as temporary:
            process = self._process(Path(temporary))
            process._account_reload_token = "old"
            process._next_account_reconcile_at = time.monotonic() + 60
            accounts = (SimpleNamespace(agent="agent-a"),)
            with (
                patch("telephony.runtime.process.read_runtime_config_reload_token", return_value="new"),
                patch("telephony.runtime.process.load_enabled_sip_accounts", return_value=accounts) as load,
            ):
                process._reconcile_accounts_if_needed()
            load.assert_called_once()
            process.runtime.reconcile_accounts.assert_called_once_with(accounts)
            self.assertEqual(process._account_reload_token, "new")

    def test_periodic_reconcile_is_safety_net_without_new_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            process = self._process(Path(temporary))
            process._account_reload_token = "same"
            process._next_account_reconcile_at = 0.0
            accounts = (SimpleNamespace(agent="agent-a"),)
            with (
                patch("telephony.runtime.process.read_runtime_config_reload_token", return_value="same"),
                patch("telephony.runtime.process.load_enabled_sip_accounts", return_value=accounts) as load,
            ):
                process._reconcile_accounts_if_needed()
            load.assert_called_once()
            process.runtime.reconcile_accounts.assert_called_once_with(accounts)


if __name__ == "__main__":
    unittest.main()
