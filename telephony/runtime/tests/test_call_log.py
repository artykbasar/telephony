from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from telephony.runtime.accounts import TelephonyRuntimeAccount
from telephony.runtime.call_log import TelephonyCallLogWriter
from telephony.voice.sip import SipAccountConfig, SipCallOutcome, SipCallState, SipIncomingCall


def _account():
    return TelephonyRuntimeAccount(
        agent="alice@example.test", user="alice@example.test", extension="2001", display_name="Alice",
        config=SipAccountConfig(server="pbx.example.test", username="2001", password="secret"),
    )


class TelephonyCallLogWriterTest(unittest.TestCase):
    def setUp(self):
        self.writer = TelephonyCallLogWriter()

    @patch("telephony.runtime.call_log.frappe")
    def test_outgoing_log_is_server_attributed_to_runtime_owner(self, frappe):
        doc = MagicMock()
        doc.start_time = None
        frappe.db.exists.return_value = None
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.return_value = datetime(2026, 8, 17, 12, 0, 0)
        self.writer.outgoing_started(_account(), "call-1", "441234")
        self.assertEqual(doc.telephony_medium, "SIP")
        self.assertEqual(doc.medium, "Native SIP")
        self.assertEqual(doc.caller, "alice@example.test")
        self.assertEqual(doc.type, "Outgoing")
        self.assertEqual(doc.to, "441234")
        self.assertEqual(doc.status, "Initiated")
        doc.save.assert_called_once_with(ignore_permissions=True)

    @patch("telephony.runtime.call_log.send_incoming_call_notification")
    @patch("telephony.runtime.call_log.frappe")
    def test_incoming_log_is_server_attributed_to_runtime_owner(self, frappe, send_push):
        doc = MagicMock()
        doc.start_time = None
        frappe.db.exists.return_value = None
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.return_value = datetime(2026, 8, 17, 12, 0, 0)
        self.writer.incoming_started(_account(), SipIncomingCall("call-2", "441111", "2001", "John Smith", "Landlord"))
        self.assertEqual(doc.receiver, "alice@example.test")
        self.assertEqual(doc.type, "Incoming")
        self.assertEqual(getattr(doc, "from"), "441111")
        self.assertEqual(doc.to, "2001")
        self.assertEqual(doc.caller_name, "John Smith")
        self.assertEqual(doc.ivr_route, "Landlord")
        self.assertEqual(doc.status, "Ringing")
        send_push.assert_called_once()
        self.assertEqual(send_push.call_args.args[0], "alice@example.test")
        self.assertEqual(send_push.call_args.args[1].call_id, "call-2")

    @patch("telephony.runtime.call_log.frappe")
    def test_connected_time_excludes_ringing_from_talk_duration(self, frappe):
        started = datetime(2026, 8, 17, 12, 0, 0)
        connected = started + timedelta(seconds=15)
        ended = connected + timedelta(seconds=42)
        doc = MagicMock(start_time=started, connected_at=None, type="Outgoing", status="Ringing")
        frappe.db.exists.return_value = "call-3"
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.side_effect = [connected, ended]

        self.writer.state_changed(_account(), "call-3", SipCallState.CONNECTED)
        self.assertEqual(doc.connected_at, connected)
        self.writer.state_changed(_account(), "call-3", SipCallState.ENDED)

        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc.duration, 42)
        self.assertEqual(doc.end_time, ended)
        self.assertEqual(doc.start_time, started)

    @patch("telephony.runtime.call_log.frappe")
    def test_unanswered_incoming_call_is_missed_with_zero_talk_duration(self, frappe):
        started = datetime(2026, 8, 17, 12, 0, 0)
        ended = started + timedelta(seconds=15)
        doc = MagicMock(start_time=started, connected_at=None, type="Incoming", status="Ringing")
        frappe.db.exists.return_value = "call-4"
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.return_value = ended

        self.writer.state_changed(_account(), "call-4", SipCallState.ENDED, SipCallOutcome.NO_ANSWER)

        self.assertEqual(doc.status, "No Answer")
        self.assertEqual(doc.duration, 0)
        self.assertEqual(doc.end_time, ended)

    @patch("telephony.runtime.call_log.frappe")
    def test_explicit_busy_outcome_is_preserved(self, frappe):
        doc = MagicMock(connected_at=None, type="Outgoing", status="Ringing")
        frappe.db.exists.return_value = "call-5"
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.return_value = datetime(2026, 8, 17, 12, 0, 20)

        self.writer.state_changed(_account(), "call-5", SipCallState.ENDED, SipCallOutcome.BUSY)

        self.assertEqual(doc.status, "Busy")
        self.assertEqual(doc.duration, 0)

    @patch("telephony.runtime.call_log.frappe")
    def test_managed_writer_owns_persistent_frappe_context(self, frappe):
        doc = MagicMock()
        doc.start_time = None
        frappe.db.exists.return_value = None
        frappe.get_doc.return_value = doc
        frappe.utils.now_datetime.return_value = datetime(2026, 8, 17, 12, 0, 0)
        with tempfile.TemporaryDirectory() as temp:
            writer = TelephonyCallLogWriter(
                site="site.test", bench_path=Path(temp), startup_timeout=0.5, shutdown_timeout=0.5
            )
            writer.start()
            writer.outgoing_started(_account(), "managed-call", "441234")
            deadline = time.time() + 0.5
            while time.time() < deadline and not doc.save.called:
                time.sleep(0.01)
            writer.stop()
        frappe.init.assert_called_once_with(site="site.test", sites_path=str(Path(temp) / "sites"))
        frappe.connect.assert_called_once()
        doc.save.assert_called_once_with(ignore_permissions=True)
        frappe.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
