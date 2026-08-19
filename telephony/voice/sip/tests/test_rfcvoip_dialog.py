from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState

from telephony.voice.sip.models import SipTransferResult
from telephony.voice.sip.rfcvoip_dialog import (
    RfcVoipDialogController,
    _dialog_headers,
    _next_dialog_cseq,
    _sdp_body,
)


class _FakeSip:
    def __init__(self):
        self.tagLibrary = {"call-a": "local-a", "call-b": "local-b"}
        self.inviteCounter = SIP.Counter(start=7)
        self.byeCounter = SIP.Counter(start=1)
        self.recvLock = threading.RLock()
        self._subscription_lock = threading.RLock()
        self.subscriptions = {}

    @staticmethod
    def _sdp_address_type(_address):
        return "IP4"

    @staticmethod
    def _dialog_remote_uri(request):
        return str(request.headers.get("Contact") or "sip:remote@example.test").strip("<>")

    @staticmethod
    def _normalize_request_target(target):
        if target.startswith("sip:"):
            return target
        return f"sip:{target}@example.test"

    @staticmethod
    def _via_header(rport=True):
        del rport
        return "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest;rport\r\n"

    @staticmethod
    def _contact_header():
        return "Contact: <sip:2099@127.0.0.1:5060;transport=UDP>\r\n"

    @staticmethod
    def dialog_target(_request):
        return "127.0.0.1", 5060


class _FakeCall:
    def __init__(self, sip, call_id="call-a", *, local_is_from=True, contact=None):
        self.sip = sip
        self.call_id = call_id
        self.session_id = "20"
        self.myIP = "127.0.0.1"
        self.state = CallState.ANSWERED
        local_tag = sip.tagLibrary[call_id]
        remote_tag = f"remote-{call_id[-1]}"
        if local_is_from:
            from_header = {"raw": "<sip:2099@example.test>", "tag": local_tag}
            to_header = {"raw": "<sip:8299@example.test>", "tag": remote_tag}
        else:
            from_header = {"raw": "<sip:8299@example.test>", "tag": remote_tag}
            to_header = {"raw": "<sip:2099@example.test>", "tag": local_tag}
        self.request = SimpleNamespace(headers={
            "Call-ID": call_id,
            "From": from_header,
            "To": to_header,
            "Contact": contact or f"<sip:8299@pbx.example.test;transport=udp>",
        })
        self.client = SimpleNamespace(
            inPort=12100 if call_id == "call-a" else 12102,
            outIP="192.0.2.10",
            outPort=20000,
            sendrecv=RTP.TransmitType.SENDRECV,
        )

    def _rtp_media_answer_map(self):
        return {
            self.client.inPort: {
                "media_type": "audio",
                "port_count": 1,
                "codecs": {0: RTP.PayloadType.PCMU},
            }
        }

    def _rtp_clients_snapshot(self):
        return [self.client]


class RfcVoipDialogTest(unittest.TestCase):
    def test_dialog_headers_reverse_incoming_dialog_orientation(self):
        sip = _FakeSip()
        call = _FakeCall(sip, local_is_from=False)

        local, remote, local_tag, remote_tag = _dialog_headers(sip, call.request)

        self.assertEqual(local, "<sip:2099@example.test>;tag=local-a")
        self.assertEqual(remote, "<sip:8299@example.test>;tag=remote-a")
        self.assertEqual(local_tag, "local-a")
        self.assertEqual(remote_tag, "remote-a")

    def test_hold_and_resume_sdp_preserve_media_and_direction(self):
        sip = _FakeSip()
        call = _FakeCall(sip)

        held = _sdp_body(call, RTP.TransmitType.SENDONLY)
        resumed = _sdp_body(call, RTP.TransmitType.SENDRECV)

        self.assertIn("m=audio 12100 RTP/AVP 0\r\n", held)
        self.assertIn("a=rtpmap:0 PCMU/8000\r\n", held)
        self.assertIn("a=sendonly\r\n", held)
        self.assertIn("a=sendrecv\r\n", resumed)
        self.assertNotEqual(held.split("\r\n")[1], resumed.split("\r\n")[1])

        sip._telephony_advertised_ip = "192.168.1.222"
        advertised = _sdp_body(call, RTP.TransmitType.SENDRECV)
        self.assertIn("IN IP4 192.168.1.222\r\n", advertised)
        self.assertNotIn("IN IP4 127.0.0.1\r\n", advertised)

    def test_in_dialog_request_has_existing_dialog_identifiers(self):
        sip = _FakeSip()
        call = _FakeCall(sip)
        controller = RfcVoipDialogController()

        request, remote_uri = controller._build_in_dialog_request(
            call,
            method="INVITE",
            cseq=9,
            body="v=0\r\n",
            content_type="application/sdp",
        )

        self.assertEqual(remote_uri, "sip:8299@pbx.example.test;transport=udp")
        self.assertIn("INVITE sip:8299@pbx.example.test;transport=udp SIP/2.0", request)
        self.assertIn("From: <sip:2099@example.test>;tag=local-a", request)
        self.assertIn("To: <sip:8299@example.test>;tag=remote-a", request)
        self.assertIn("Call-ID: call-a", request)
        self.assertIn("CSeq: 9 INVITE", request)
        self.assertIn("Content-Type: application/sdp", request)

    def test_dialog_cseq_advances_later_bye_counter(self):
        sip = _FakeSip()

        first = _next_dialog_cseq(sip)
        second = _next_dialog_cseq(sip)

        self.assertEqual((first, second), (7, 8))
        self.assertGreaterEqual(sip.byeCounter.current(), 9)

    def test_attended_transfer_uses_contact_and_escaped_replaces(self):
        sip = _FakeSip()
        original = _FakeCall(sip, "call-a")
        consult = _FakeCall(
            sip,
            "call-b",
            contact="<sip:2098@192.0.2.20:5060;transport=udp>",
        )
        controller = RfcVoipDialogController()
        expected = SipTransferResult(
            mode="attended",
            target="test",
            accepted=True,
            completed=True,
            status_code=200,
            phrase="OK",
            replaces_call_id="call-b",
        )

        with patch.object(controller, "_refer", return_value=expected) as refer:
            result = controller.attended_transfer(original, consult)

        self.assertIs(result, expected)
        refer_to = refer.call_args.args[1]
        self.assertTrue(refer_to.startswith("sip:2098@192.0.2.20:5060;transport=udp?Replaces="))
        self.assertIn("call-b%3Bto-tag%3Dremote-b%3Bfrom-tag%3Dlocal-b", refer_to)
        self.assertEqual(refer.call_args.kwargs["replaces_call_id"], "call-b")

    def test_attended_transfer_replaces_uses_literal_to_from_tags_for_incoming_consult(self):
        sip = _FakeSip()
        original = _FakeCall(sip, "call-a")
        consult = _FakeCall(sip, "call-b", local_is_from=False)
        controller = RfcVoipDialogController()
        expected = SipTransferResult(
            mode="attended", target="test", accepted=True, completed=True,
            status_code=200, phrase="OK", replaces_call_id="call-b",
        )

        with patch.object(controller, "_refer", return_value=expected) as refer:
            controller.attended_transfer(original, consult)

        refer_to = refer.call_args.args[1]
        self.assertIn("call-b%3Bto-tag%3Dlocal-b%3Bfrom-tag%3Dremote-b", refer_to)

    def test_blind_transfer_rejects_header_injection_target(self):
        sip = _FakeSip()
        call = _FakeCall(sip)
        controller = RfcVoipDialogController()

        with self.assertRaisesRegex(Exception, "target is invalid"):
            controller.transfer(call, "2098\r\nX-Evil: injected")

    def test_refer_sipfrag_terminal_status(self):
        controller = RfcVoipDialogController()
        self.assertEqual(controller._sipfrag_status("SIP/2.0 100 Trying\r\n"), (100, "Trying"))
        self.assertEqual(controller._sipfrag_status("SIP/2.0 200 OK\r\n"), (200, "OK"))
        self.assertEqual(controller._sipfrag_status("SIP/2.0 486 Busy Here\r\n"), (486, "Busy Here"))
        self.assertEqual(controller._sipfrag_status("not sipfrag"), (None, None))

    def test_early_final_notify_is_consumed_after_refer_acceptance(self):
        sip = _FakeSip()
        call = _FakeCall(sip)
        controller = RfcVoipDialogController(transfer_timeout=5)

        def accepted_with_early_notify(_call, refer_to, subscription, **_kwargs):
            subscription.last_notify_body = "SIP/2.0 200 OK\r\n"
            subscription.subscription_state = "terminated"
            return SipTransferResult(
                mode="blind",
                target=refer_to,
                accepted=True,
                completed=False,
                status_code=202,
                phrase="Accepted",
            )

        with patch.object(controller, "_send_refer", side_effect=accepted_with_early_notify):
            result = controller.transfer(call, "2098")

        self.assertTrue(result.accepted)
        self.assertTrue(result.completed)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.phrase, "OK")
        self.assertNotIn("call-a", sip.subscriptions)

    def test_repeated_refer_tracks_second_dialog_attempt_for_event_id(self):
        sip = _FakeSip()
        call = _FakeCall(sip)
        controller = RfcVoipDialogController(transfer_timeout=5)
        ordinals = []

        def accepted(_call, refer_to, subscription, **kwargs):
            ordinals.append(kwargs["refer_ordinal"])
            subscription.last_notify_body = "SIP/2.0 200 OK\r\n"
            subscription.subscription_state = "terminated"
            return SipTransferResult(
                mode="blind", target=refer_to, accepted=True, completed=False,
                status_code=202, phrase="Accepted",
            )

        with patch.object(controller, "_send_refer", side_effect=accepted):
            controller.transfer(call, "2098")
            controller.transfer(call, "2097")

        self.assertEqual(ordinals, [1, 2])


if __name__ == "__main__":
    unittest.main()
