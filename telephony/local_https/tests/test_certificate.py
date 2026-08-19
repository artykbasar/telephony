from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cryptography import x509

from telephony.local_https.certificate import ensure_self_signed_certificate


class LocalHttpsCertificateTest(unittest.TestCase):
    def test_generates_site_private_self_signed_certificate_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            first = ensure_self_signed_certificate(
                site="site.test", bench_path=bench, host="192.168.1.222"
            )
            certificate = x509.load_pem_x509_certificate(first.certificate_path.read_bytes())
            self.assertEqual(certificate.subject, certificate.issuer)
            self.assertTrue(first.generated)
            self.assertEqual(os.stat(first.private_key_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(first.certificate_path.parent).st_mode & 0o777, 0o700)

            second = ensure_self_signed_certificate(
                site="site.test", bench_path=bench, host="192.168.1.222"
            )
            self.assertFalse(second.generated)
            self.assertEqual(second.fingerprint_sha256, first.fingerprint_sha256)

    def test_host_change_regenerates_certificate(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            first = ensure_self_signed_certificate(
                site="site.test", bench_path=bench, host="192.168.1.222"
            )
            second = ensure_self_signed_certificate(
                site="site.test", bench_path=bench, host="telephony.localhost"
            )
            self.assertTrue(second.generated)
            self.assertNotEqual(second.fingerprint_sha256, first.fingerprint_sha256)
