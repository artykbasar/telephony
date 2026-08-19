from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from telephony.voice.wss.auth import VoiceTicketError, create_ticket, verify_ticket


class VoiceTicketTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bench_path = Path(self.temp.name)
        self.site = "voice.test"
        site_path = self.bench_path / "sites" / self.site
        site_path.mkdir(parents=True)
        (site_path / "site_config.json").write_text(json.dumps({"encryption_key": "test-secret-key"}))

    def tearDown(self):
        self.temp.cleanup()

    def test_ticket_round_trip_and_site_binding(self):
        ticket = create_ticket(site=self.site, user="agent@example.test", bench_path=self.bench_path)
        payload = verify_ticket(ticket, bench_path=self.bench_path, expected_site=self.site)
        self.assertEqual(payload["user"], "agent@example.test")
        self.assertEqual(payload["site"], self.site)
        with self.assertRaises(VoiceTicketError):
            verify_ticket(ticket, bench_path=self.bench_path, expected_site="other.test")

    def test_tampered_and_expired_tickets_are_rejected(self):
        ticket = create_ticket(site=self.site, user="agent@example.test", bench_path=self.bench_path, ttl_seconds=5)
        encoded, signature = ticket.split(".", 1)
        tampered = encoded[:-1] + ("A" if encoded[-1] != "A" else "B") + "." + signature
        with self.assertRaises(VoiceTicketError):
            verify_ticket(tampered, bench_path=self.bench_path)
        payload = verify_ticket(ticket, bench_path=self.bench_path)
        with self.assertRaises(VoiceTicketError):
            verify_ticket(ticket, bench_path=self.bench_path, now=int(payload["exp"]) + 1)

    def test_ticket_secret_is_telephony_specific(self):
        ticket = create_ticket(site=self.site, user="agent@example.test", bench_path=self.bench_path)
        self.assertNotIn("test-secret-key", ticket)
        self.assertEqual(ticket.count("."), 1)


if __name__ == "__main__":
    unittest.main()
