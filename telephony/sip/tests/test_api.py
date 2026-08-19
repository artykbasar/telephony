from types import SimpleNamespace
import unittest
from unittest.mock import patch

import telephony.sip.api as api


class SipApiTest(unittest.TestCase):
    def test_status_compatibility_mapping_remains_available(self):
        self.assertEqual(api._resolve_status("connected"), "In Progress")
        self.assertEqual(api._resolve_status("missed"), "No Answer")
        self.assertEqual(api._resolve_status("cancelled"), "Canceled")

    @patch.object(api.frappe, "get_cached_doc")
    @patch.object(api, "_get_voice_identity")
    def test_browser_config_contains_no_sip_credentials_or_pbx_network_config(self, identity, get_settings):
        get_settings.return_value = SimpleNamespace(enabled=1)
        identity.return_value = {"user": "alice@example.test", "extension": "2001", "display_name": "Alice"}
        with patch.object(api.frappe, "session", SimpleNamespace(user="alice@example.test")):
            result = api.fetch_my_sip_config()
        self.assertTrue(result["enabled"])
        serialized = repr(result).lower()
        for forbidden in ("password", "sip_server", "wss_uri", "stun", "turn_servers", "realm"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(result["transport"], "socketio")
        self.assertTrue(result["capabilities"]["server_side_sip"])


if __name__ == "__main__":
    unittest.main()
