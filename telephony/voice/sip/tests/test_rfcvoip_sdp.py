from __future__ import annotations

import unittest

from telephony.voice.sip.rfcvoip_sdp import _rewrite_sdp_body, _rewrite_sip_sdp


class RfcVoipAdvertisedSdpTest(unittest.TestCase):
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
