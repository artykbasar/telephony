from __future__ import annotations

import unittest
from types import SimpleNamespace

from rfcvoip import SIP

from telephony.voice.sip.rfcvoip_sdp import (
    _learn_advertised_ip_from_register_response,
    _rewrite_sdp_body,
    _rewrite_sip_sdp,
    effective_advertised_ip,
)


class RfcVoipAdvertisedSdpTest(unittest.TestCase):
    def test_successful_register_learns_server_observed_ip_for_sdp(self):
        client = SimpleNamespace()
        response = SimpleNamespace(
            type=SIP.SIPMessageType.RESPONSE,
            status=SIP.SIPStatus.OK,
            headers={
                "CSeq": {"method": "REGISTER"},
                "Via": [{"address": ("0.0.0.0", "5060"), "received": "172.19.0.14", "rport": "37988"}],
            },
        )
        learned = _learn_advertised_ip_from_register_response(client, response)
        self.assertEqual(learned, "172.19.0.14")
        self.assertEqual(client._telephony_advertised_ip, "172.19.0.14")
        self.assertEqual(client._telephony_advertised_ip_source, "register-received")
        self.assertEqual(effective_advertised_ip(client, "0.0.0.0"), ("172.19.0.14", "register-received"))

    def test_configured_advertised_ip_wins_over_register_received(self):
        client = SimpleNamespace(
            _telephony_advertised_ip="203.0.113.20",
            _telephony_advertised_ip_source="configured",
        )
        response = SimpleNamespace(
            type=SIP.SIPMessageType.RESPONSE,
            status=SIP.SIPStatus.OK,
            headers={"CSeq": {"method": "REGISTER"}, "Via": [{"received": "172.19.0.14"}]},
        )
        learned = _learn_advertised_ip_from_register_response(client, response)
        self.assertEqual(learned, "203.0.113.20")
        self.assertEqual(client._telephony_advertised_ip, "203.0.113.20")
        self.assertEqual(client._telephony_advertised_ip_source, "configured")

    def test_register_refresh_updates_a_previously_learned_address(self):
        client = SimpleNamespace(
            _telephony_advertised_ip="172.19.0.14",
            _telephony_advertised_ip_source="register-received",
        )
        response = SimpleNamespace(
            type=SIP.SIPMessageType.RESPONSE,
            status=SIP.SIPStatus.OK,
            headers={"CSeq": {"method": "REGISTER"}, "Via": [{"received": "172.19.0.15"}]},
        )
        self.assertEqual(_learn_advertised_ip_from_register_response(client, response), "172.19.0.15")
        self.assertEqual(client._telephony_advertised_ip, "172.19.0.15")

    def test_wildcard_received_address_is_never_used_for_active_sdp(self):
        client = SimpleNamespace()
        response = SimpleNamespace(
            type=SIP.SIPMessageType.RESPONSE,
            status=SIP.SIPStatus.OK,
            headers={"CSeq": {"method": "REGISTER"}, "Via": [{"received": "0.0.0.0"}]},
        )
        self.assertEqual(_learn_advertised_ip_from_register_response(client, response), "")
        self.assertEqual(effective_advertised_ip(client, "0.0.0.0"), ("", "unresolved"))

    def test_rewrites_wildcard_origin_and_connection_address(self):
        body = (
            "v=0\r\n"
            "o=rfcvoip 1 3 IN IP4 0.0.0.0\r\n"
            "s=rfcvoip\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            "t=0 0\r\n"
            "m=audio 12104 RTP/AVP 0\r\n"
            "a=sendrecv\r\n"
        )
        rewritten = _rewrite_sdp_body(body, "192.168.1.222")
        self.assertIn("o=rfcvoip 1 3 IN IP4 192.168.1.222\r\n", rewritten)
        self.assertIn("c=IN IP4 192.168.1.222\r\n", rewritten)
        self.assertNotIn("IN IP4 0.0.0.0", rewritten)
        self.assertIn("a=sendrecv\r\n", rewritten)

    def test_sip_content_length_tracks_rewritten_sdp(self):
        body = "v=0\r\no=rfcvoip 1 3 IN IP4 0.0.0.0\r\nc=IN IP4 0.0.0.0\r\n"
        message = (
            "INVITE sip:8202@example.test SIP/2.0\r\n"
            "Content-Type: application/sdp\r\n"
            f"Content-Length: {len(body.encode('utf8'))}\r\n\r\n"
            + body
        )
        rewritten = _rewrite_sip_sdp(message, "192.168.1.222")
        headers, rewritten_body = rewritten.split("\r\n\r\n", 1)
        self.assertIn(f"Content-Length: {len(rewritten_body.encode('utf8'))}", headers)
        self.assertIn("c=IN IP4 192.168.1.222", rewritten_body)


if __name__ == "__main__":
    unittest.main()
