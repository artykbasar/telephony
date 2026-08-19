from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telephony.ftelephony.doctype.tp_sip_settings import tp_sip_settings


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "ftelephony" / "doctype" / "tp_sip_settings"


class LocalHttpsSettingsTest(unittest.TestCase):
    def test_sip_settings_exposes_local_https_controls(self):
        data = json.loads((SETTINGS / "tp_sip_settings.json").read_text())
        fields = {field["fieldname"]: field for field in data["fields"]}
        for name in (
            "local_https_section",
            "enable_local_https",
            "local_https_host",
            "local_https_port",
            "local_https_status_html",
        ):
            self.assertIn(name, fields)
            self.assertIn(name, data["field_order"])
        self.assertEqual(fields["local_https_port"].get("default"), "8443")
        self.assertNotIn("mandatory_depends_on", fields["wss_uri"])
        self.assertTrue(fields["wss_uri"].get("hidden"))

    def test_form_script_polls_status_and_opens_testing_site(self):
        script = (SETTINGS / "tp_sip_settings.js").read_text()
        self.assertIn('frappe.ui.form.on("TP SIP Settings"', script)
        self.assertIn("get_local_https_status", script)
        self.assertIn('__("Open Testing Site")', script)
        self.assertIn("telephony_poll_local_https_status", script)

    def test_settings_sync_writes_site_private_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "private" / "telephony" / "local_https"
            settings = SimpleNamespace(
                enable_local_https=1,
                local_https_host="192.168.1.222",
                local_https_port=8004,
            )
            with patch.object(tp_sip_settings, "_local_https_directory", return_value=directory):
                payload = tp_sip_settings.sync_local_https_runtime_config(settings)
            path = directory / "config.json"
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text()), payload)
            self.assertEqual(payload["enabled"], True)
            self.assertEqual(payload["host"], "192.168.1.222")
            self.assertEqual(payload["port"], 8004)
            self.assertEqual(payload["bind_address"], "0.0.0.0")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)

    def test_settings_sync_can_disable_managed_https(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "private" / "telephony" / "local_https"
            settings = SimpleNamespace(
                enable_local_https=0,
                local_https_host="192.168.1.222",
                local_https_port=8443,
            )
            with patch.object(tp_sip_settings, "_local_https_directory", return_value=directory):
                payload = tp_sip_settings.sync_local_https_runtime_config(settings)
            self.assertFalse(payload["enabled"])


if __name__ == "__main__":
    unittest.main()
