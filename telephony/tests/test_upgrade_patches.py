from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telephony.patches.v1_0 import migrate_webrtc_sip_to_native as migrate
from telephony.patches.v1_0 import remove_sip_telemetry as remove_telemetry


def fake_db() -> SimpleNamespace:
    return SimpleNamespace(
        set_single_value=Mock(),
        set_value=Mock(),
        commit=Mock(),
        exists=Mock(),
        sql_ddl=Mock(),
    )


class NativeSipUpgradePatchTest(unittest.TestCase):
    def test_legacy_sip_host_extracts_websocket_hostname(self):
        self.assertEqual(migrate.legacy_sip_host("wss://pbx.example.com:8089/ws"), "pbx.example.com")
        self.assertEqual(migrate.legacy_sip_host("pbx.example.com:8089/ws"), "pbx.example.com")
        self.assertEqual(migrate.legacy_sip_host(""), "")

    def test_existing_native_configuration_is_never_overwritten(self):
        settings = SimpleNamespace(enabled=1, sip_server="native.example.com")
        db = fake_db()
        with patch.object(migrate.frappe, "get_single", return_value=settings), patch.object(
            migrate.frappe, "get_all"
        ) as get_all, patch.object(migrate.frappe, "db", db):
            migrate.execute()
        get_all.assert_not_called()
        db.set_single_value.assert_not_called()

    def test_legacy_wss_configuration_becomes_native_sip_configuration(self):
        settings = SimpleNamespace(
            enabled=1,
            sip_server="",
            wss_uri="wss://pbx.example.com:8089/ws",
            sip_port=None,
            transport=None,
            local_bind_address=None,
        )
        agents = [SimpleNamespace(name="Agent A", sip_server=None, override_sip_settings=0, sip_override_server=None)]
        db = fake_db()
        with patch.object(migrate.frappe, "get_single", return_value=settings), patch.object(
            migrate.frappe, "get_all", return_value=agents
        ), patch.object(migrate.frappe, "db", db):
            migrate.execute()

        values = {call.args[1]: call.args[2] for call in db.set_single_value.call_args_list}
        self.assertEqual(values["sip_server"], "pbx.example.com")
        self.assertEqual(values["sip_port"], 5060)
        self.assertEqual(values["transport"], "UDP")
        self.assertEqual(values["local_bind_address"], "0.0.0.0")
        db.set_value.assert_not_called()
        db.commit.assert_called_once()

    def test_legacy_agent_wss_override_becomes_native_override(self):
        settings = SimpleNamespace(
            enabled=1,
            sip_server="",
            wss_uri="wss://pbx.example.com:8089/ws",
            sip_port=5070,
            transport="TCP",
            local_bind_address="0.0.0.0",
        )
        agents = [
            SimpleNamespace(
                name="Agent B",
                sip_server="wss://other-pbx.example.com:8089/ws",
                override_sip_settings=0,
                sip_override_server=None,
            )
        ]
        db = fake_db()
        with patch.object(migrate.frappe, "get_single", return_value=settings), patch.object(
            migrate.frappe, "get_all", return_value=agents
        ), patch.object(migrate.frappe, "db", db):
            migrate.execute()

        values = db.set_value.call_args.args[2]
        self.assertEqual(values["sip_override_server"], "other-pbx.example.com")
        self.assertEqual(values["sip_override_port"], 5060)
        self.assertEqual(values["sip_override_transport"], "UDP")

    def test_legacy_enabled_site_without_any_pbx_host_is_disabled_safely(self):
        settings = SimpleNamespace(enabled=1, sip_server="", wss_uri="")
        db = fake_db()
        with patch.object(migrate.frappe, "get_single", return_value=settings), patch.object(
            migrate.frappe, "get_all", return_value=[]
        ), patch.object(migrate.frappe, "db", db):
            migrate.execute()
        db.set_single_value.assert_called_once_with("TP SIP Settings", "enabled", 0)
        db.commit.assert_called_once()


class RemoveSipTelemetryPatchTest(unittest.TestCase):
    def test_patch_removes_doctype_metadata_and_table(self):
        db = fake_db()
        db.exists.return_value = "TP SIP Telemetry Event"
        with patch.object(remove_telemetry.frappe, "db", db), patch.object(
            remove_telemetry.frappe, "delete_doc"
        ) as delete_doc, patch.object(remove_telemetry.frappe, "clear_cache"):
            remove_telemetry.execute()
        delete_doc.assert_called_once()
        db.sql_ddl.assert_called_once_with("DROP TABLE IF EXISTS `tabTP SIP Telemetry Event`")


if __name__ == "__main__":
    unittest.main()
