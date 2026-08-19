from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from telephony.local_https.config import (
    format_https_url,
    load_local_https_runtime_config,
    local_https_config_path,
    normalise_local_https_host,
)


class LocalHttpsConfigTest(unittest.TestCase):
    def test_normalises_hosts_and_formats_ipv6_url(self):
        self.assertEqual(normalise_local_https_host(" Example.TEST. "), "example.test")
        self.assertEqual(normalise_local_https_host("[2001:db8::1]"), "2001:db8::1")
        self.assertEqual(format_https_url("2001:db8::1", 8443), "https://[2001:db8::1]:8443")
        with self.assertRaises(ValueError):
            normalise_local_https_host("https://example.test")
        with self.assertRaises(ValueError):
            normalise_local_https_host("0.0.0.0")

    def test_loads_enabled_site_private_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            path = local_https_config_path(site="site.test", bench_path=bench)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "enabled": True,
                "host": "192.168.1.222",
                "port": 8443,
                "bind_address": "0.0.0.0",
            }))
            config = load_local_https_runtime_config(site="site.test", bench_path=bench)
            self.assertTrue(config.enabled)
            self.assertEqual(config.url, "https://192.168.1.222:8443")
