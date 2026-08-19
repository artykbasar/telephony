from __future__ import annotations

import asyncio
import json
import os
import signal
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

from telephony.local_https.certificate import ensure_self_signed_certificate
from telephony.local_https.config import (
    LocalHttpsRuntimeConfig,
    local_https_status_path,
    load_local_https_runtime_config,
)

_MAX_HEADER_BYTES = 64 * 1024


@dataclass(frozen=True)
class _TcpTarget:
    host: str
    port: int


class LocalHttpsProxy:
    def __init__(self, *, site: str, bench_path: Path, config: LocalHttpsRuntimeConfig) -> None:
        self.site = site
        self.bench_path = Path(bench_path).resolve()
        self.config = config
        common = self._read_json(self.bench_path / "sites" / "common_site_config.json")
        self.web_target = _TcpTarget(
            self._loopback_host(common.get("webserver_host")),
            int(common.get("webserver_port") or 8000),
        )
        self.socketio_target = _TcpTarget(
            "127.0.0.1",
            int(common.get("socketio_port") or 9000),
        )
        self._server: asyncio.Server | None = None
        self._stop_event: asyncio.Event | None = None
        self._certificate_fingerprint = ""

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _loopback_host(value: object) -> str:
        host = str(value or "127.0.0.1").strip()
        return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host

    async def run_until_config_change(self, stop_event: asyncio.Event) -> str:
        certificate = ensure_self_signed_certificate(
            site=self.site,
            bench_path=self.bench_path,
            host=self.config.host,
        )
        self._certificate_fingerprint = certificate.fingerprint_sha256
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        context.load_cert_chain(
            certfile=str(certificate.certificate_path),
            keyfile=str(certificate.private_key_path),
        )
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self.config.bind_address,
                port=self.config.port,
                ssl=context,
                start_serving=True,
            )
        except OSError as exc:
            self._write_status("error", f"Unable to listen on port {self.config.port}: {exc}")
            return "error"
        self._write_status("ready", "Local HTTPS testing endpoint is ready.")
        print(
            f"TELEPHONY_LOCAL_HTTPS_READY site={self.site} url={self.config.url} "
            f"bind={self.config.bind_address}:{self.config.port}",
            flush=True,
        )
        config_task = asyncio.create_task(self._wait_for_config_change(stop_event))
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                {config_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            reason = "stop" if stop_task in done and stop_task.result() else "config_changed"
        finally:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._write_status("stopped", "Local HTTPS testing endpoint is stopped.")
        return reason

    async def _wait_for_config_change(self, stop_event: asyncio.Event) -> LocalHttpsRuntimeConfig:
        while not stop_event.is_set():
            await asyncio.sleep(0.25)
            current = load_local_https_runtime_config(site=self.site, bench_path=self.bench_path)
            if current.signature != self.config.signature:
                return current
        return self.config

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
            if len(header) > _MAX_HEADER_BYTES:
                raise ValueError("HTTP request headers are too large.")
            path = self._request_path(header)
            target = self._target_for_path(path)
            upstream_reader, upstream_writer = await asyncio.wait_for(
                self._open_target(target), timeout=3.0
            )
            upstream_writer.write(self._proxy_headers(header, path=path))
            await upstream_writer.drain()
            client_to_upstream = asyncio.create_task(self._pipe(reader, upstream_writer))
            upstream_to_client = asyncio.create_task(self._pipe(upstream_reader, writer))
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            if not writer.is_closing():
                try:
                    writer.write(
                        b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n"
                        b"Content-Type: text/plain; charset=utf-8\r\nContent-Length: 26\r\n\r\n"
                        b"Telephony local proxy error."
                    )
                    await writer.drain()
                except (ConnectionError, OSError):
                    pass
            print(
                f"TELEPHONY_LOCAL_HTTPS_PROXY_ERROR site={self.site} error={type(exc).__name__}: {exc}",
                flush=True,
            )
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            data = await reader.read(64 * 1024)
            if not data:
                return
            writer.write(data)
            await writer.drain()

    @staticmethod
    def _request_path(header: bytes) -> str:
        first_line = header.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ")
        if len(parts) < 2:
            raise ValueError("Malformed HTTP request line.")
        return parts[1].decode("ascii", errors="strict").split("?", 1)[0]

    def _target_for_path(self, path: str) -> _TcpTarget:
        if path == "/socket.io" or path.startswith("/socket.io/"):
            return self.socketio_target
        return self.web_target

    async def _open_target(
        self, target: _TcpTarget
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection(target.host, target.port)

    def _proxy_headers(self, header: bytes, *, path: str | None = None) -> bytes:
        lines = header[:-4].split(b"\r\n")
        if not lines:
            return header
        filtered = [lines[0]]
        forwarded_host = b""
        had_origin = False
        websocket_upgrade = False
        blocked = {
            b"connection",
            b"keep-alive",
            b"proxy-connection",
            b"x-frappe-site-name",
            b"x-forwarded-proto",
            b"x-forwarded-port",
            b"x-forwarded-host",
        }
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator:
                continue
            lowered = name.strip().lower()
            if lowered == b"host":
                forwarded_host = value.strip()
                continue
            if lowered == b"origin":
                had_origin = True
                continue
            if lowered == b"upgrade" and b"websocket" in value.strip().lower():
                websocket_upgrade = True
            if lowered in blocked:
                continue
            filtered.append(line)
        filtered.append(b"Host: " + self.site.encode("utf-8"))
        filtered.append(b"Connection: Upgrade" if websocket_upgrade else b"Connection: close")
        socketio_path = path == "/socket.io" or str(path or "").startswith("/socket.io/")
        if socketio_path:
            filtered.append(
                f"Origin: http://{self.site}:{self.web_target.port}".encode("utf-8")
            )
        elif had_origin:
            filtered.append(b"Origin: https://" + self.site.encode("utf-8"))
        if forwarded_host:
            filtered.append(b"X-Forwarded-Host: " + forwarded_host)
        filtered.extend([
            b"X-Forwarded-Proto: https",
            f"X-Forwarded-Port: {self.config.port}".encode("ascii"),
            b"X-Frappe-Site-Name: " + self.site.encode("utf-8"),
        ])
        return b"\r\n".join(filtered) + b"\r\n\r\n"

    def _write_status(self, state: str, message: str) -> None:
        path = local_https_status_path(site=self.site, bench_path=self.bench_path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        payload = {
            "state": state,
            "message": message,
            "pid": os.getpid(),
            "site": self.site,
            "host": self.config.host,
            "port": self.config.port,
            "bind_address": self.config.bind_address,
            "url": self.config.url,
            "certificate_fingerprint_sha256": self._certificate_fingerprint,
            "updated_at": time.time(),
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)


class LocalHttpsService:
    def __init__(
        self,
        *,
        site: str,
        bench_path: Path,
        poll_interval: float = 0.25,
        install_signal_handlers: bool = True,
    ) -> None:
        self.site = site
        self.bench_path = Path(bench_path).resolve()
        self.poll_interval = max(0.05, float(poll_interval))
        self.install_signal_handlers = install_signal_handlers
        self.stop_event: asyncio.Event | None = None

    async def run(self) -> int:
        self.stop_event = asyncio.Event()
        if self.install_signal_handlers:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except (NotImplementedError, RuntimeError):
                    pass

        while not self.stop_event.is_set():
            config = load_local_https_runtime_config(site=self.site, bench_path=self.bench_path)
            if not config.enabled:
                self._write_idle_status(config)
                await self._wait_for_config_change(config.signature)
                continue

            proxy = LocalHttpsProxy(site=self.site, bench_path=self.bench_path, config=config)
            reason = await proxy.run_until_config_change(self.stop_event)
            if reason == "stop":
                break
            if reason == "error":
                await self._wait_for_config_change(config.signature, timeout=2.0)

        return 0


    def request_stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()

    async def _wait_for_config_change(self, signature: str, timeout: float | None = None) -> None:
        started = time.monotonic()
        while self.stop_event is not None and not self.stop_event.is_set():
            await asyncio.sleep(self.poll_interval)
            current = load_local_https_runtime_config(site=self.site, bench_path=self.bench_path)
            if current.signature != signature:
                return
            if timeout is not None and time.monotonic() - started >= timeout:
                return

    def _write_idle_status(self, config: LocalHttpsRuntimeConfig) -> None:
        path = local_https_status_path(site=self.site, bench_path=self.bench_path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "state": "disabled",
            "message": "Local HTTPS testing endpoint is disabled.",
            "pid": os.getpid(),
            "site": self.site,
            "host": config.host,
            "port": config.port,
            "bind_address": config.bind_address,
            "url": config.url,
            "updated_at": time.time(),
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)


def run_local_https_proxy(*, site: str, bench_path: Path) -> int:
    config = load_local_https_runtime_config(site=site, bench_path=bench_path)
    if not config.enabled:
        raise RuntimeError("Local HTTPS testing is not enabled for this site.")
    return asyncio.run(LocalHttpsService(site=site, bench_path=bench_path).run())
