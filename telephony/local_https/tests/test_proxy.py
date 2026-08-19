from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from telephony.local_https.config import LocalHttpsRuntimeConfig, local_https_config_path
from telephony.local_https.proxy import LocalHttpsProxy, LocalHttpsService, _TcpTarget


class LocalHttpsProxyTest(unittest.TestCase):
    def make_proxy(self):
        temp = tempfile.TemporaryDirectory()
        bench = Path(temp.name)
        (bench / "sites").mkdir()
        (bench / "sites" / "common_site_config.json").write_text(
            '{"webserver_port": 8001, "socketio_port": 9001}'
        )
        proxy = LocalHttpsProxy(
            site="site.test",
            bench_path=bench,
            config=LocalHttpsRuntimeConfig(True, "192.168.1.222", 8443, "0.0.0.0"),
        )
        return temp, proxy

    def test_routes_web_and_socketio_without_public_voice_socket(self):
        temp, proxy = self.make_proxy()
        self.addCleanup(temp.cleanup)
        self.assertIsInstance(proxy._target_for_path("/app"), _TcpTarget)
        self.assertEqual(proxy._target_for_path("/socket.io/"), proxy.socketio_target)
        self.assertEqual(proxy._target_for_path("/telephony-voice"), proxy.web_target)

    def test_injects_secure_proxy_and_site_headers(self):
        temp, proxy = self.make_proxy()
        self.addCleanup(temp.cleanup)
        header = (
            b"GET /app HTTP/1.1\r\nHost: 192.168.1.222:8443\r\n"
            b"Origin: https://192.168.1.222:8443\r\n"
            b"X-Forwarded-Proto: http\r\nConnection: keep-alive\r\n\r\n"
        )
        proxied = proxy._proxy_headers(header)
        self.assertIn(b"X-Forwarded-Proto: https", proxied)
        self.assertIn(b"X-Frappe-Site-Name: site.test", proxied)
        self.assertIn(b"\r\nHost: site.test\r\n", proxied)
        self.assertIn(b"\r\nOrigin: https://site.test\r\n", proxied)
        self.assertNotIn(b"Origin: https://192.168.1.222:8443", proxied)
        self.assertIn(b"X-Forwarded-Host: 192.168.1.222:8443", proxied)
        self.assertIn(b"\r\nConnection: close\r\n", proxied)
        self.assertNotIn(b"Connection: keep-alive", proxied)
        self.assertEqual(proxy._request_path(header), "/app")

    def test_socketio_origin_uses_internal_dev_web_origin(self):
        temp, proxy = self.make_proxy()
        self.addCleanup(temp.cleanup)
        header = (
            b"GET /socket.io/?EIO=4&transport=websocket HTTP/1.1\r\n"
            b"Host: 192.168.1.222:8443\r\n"
            b"Origin: https://192.168.1.222:8443\r\n"
            b"Connection: keep-alive, Upgrade\r\nUpgrade: websocket\r\n\r\n"
        )
        proxied = proxy._proxy_headers(header, path="/socket.io/")
        self.assertIn(b"\r\nHost: site.test\r\n", proxied)
        self.assertIn(b"\r\nOrigin: http://site.test:8001\r\n", proxied)
        self.assertIn(b"\r\nConnection: Upgrade\r\n", proxied)
        self.assertIn(b"\r\nUpgrade: websocket\r\n", proxied)
        self.assertNotIn(b"Connection: keep-alive", proxied)

    def test_socketio_origin_is_synthesized_when_browser_omits_it(self):
        temp, proxy = self.make_proxy()
        self.addCleanup(temp.cleanup)
        header = (
            b"GET /socket.io/?EIO=4&transport=polling HTTP/1.1\r\n"
            b"Host: 192.168.1.222:8443\r\nConnection: keep-alive\r\n\r\n"
        )
        proxied = proxy._proxy_headers(header, path="/socket.io/")
        self.assertIn(b"\r\nHost: site.test\r\n", proxied)
        self.assertIn(b"\r\nOrigin: http://site.test:8001\r\n", proxied)
        self.assertIn(b"\r\nConnection: close\r\n", proxied)
        self.assertNotIn(b"Connection: keep-alive", proxied)


class LocalHttpsServiceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bench = Path(self.temp.name)
        (self.bench / "sites" / "site.test" / "private" / "telephony" / "local_https").mkdir(parents=True)
        (self.bench / "sites" / "common_site_config.json").write_text(
            '{"webserver_port": 8001, "socketio_port": 9001}'
        )
        self.port_a = self._free_port()
        self.port_b = self._free_port(exclude={self.port_a})

    @staticmethod
    def _free_port(exclude=None):
        exclude = exclude or set()
        while True:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            if port >= 1024 and port not in exclude:
                return port

    def _write_config(self, *, enabled, port):
        path = local_https_config_path(site="site.test", bench_path=self.bench)
        path.write_text(json.dumps({
            "enabled": enabled,
            "host": "127.0.0.1",
            "port": port,
            "bind_address": "127.0.0.1",
        }))

    async def _wait_port(self, port, expected, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock = socket.socket()
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                open_now = True
            else:
                open_now = False
            finally:
                sock.close()
            if open_now is expected:
                return
            await asyncio.sleep(0.05)
        self.fail(f"port {port} did not become {'open' if expected else 'closed'}")

    async def test_service_rebinds_on_port_change_and_closes_when_disabled(self):
        self._write_config(enabled=True, port=self.port_a)
        service = LocalHttpsService(
            site="site.test",
            bench_path=self.bench,
            poll_interval=0.05,
            install_signal_handlers=False,
        )
        task = asyncio.create_task(service.run())
        try:
            await self._wait_port(self.port_a, True)

            self._write_config(enabled=True, port=self.port_b)
            await self._wait_port(self.port_a, False)
            await self._wait_port(self.port_b, True)

            self._write_config(enabled=False, port=self.port_b)
            await self._wait_port(self.port_b, False)
        finally:
            service.request_stop()
            await asyncio.wait_for(task, timeout=2.0)
