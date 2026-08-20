from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path

from telephony.runtime.accounts import load_enabled_sip_accounts
from telephony.runtime.call_log import TelephonyCallLogWriter
from telephony.runtime.config_reload import read_runtime_config_reload_token
from telephony.runtime.service import TelephonySipRuntime
from telephony.runtime.socket_path import runtime_status_path, voice_websocket_socket_path
from telephony.voice.wss.server import TelephonyVoiceWebSocketServer



class TelephonyRuntimeProcess:
    def __init__(
        self,
        *,
        site: str,
        bench_path: Path,
        install_signal_handlers: bool = True,
        account_reconcile_interval: float = 60.0,
    ) -> None:
        self.site = site
        self.bench_path = Path(bench_path).resolve()
        self.install_signal_handlers = install_signal_handlers
        self.stop_event = threading.Event()
        self.runtime: TelephonySipRuntime | None = None
        self.voice_server: TelephonyVoiceWebSocketServer | None = None
        self.started_at = time.time()
        self.account_reconcile_interval = max(1.0, float(account_reconcile_interval))
        self._account_reload_token = ""
        self._next_account_reconcile_at = 0.0

    def request_stop(self) -> None:
        self.stop_event.set()

    def run(self) -> int:
        self._account_reload_token = read_runtime_config_reload_token(
            site=self.site, bench_path=self.bench_path
        )
        accounts = load_enabled_sip_accounts(site=self.site, bench_path=self.bench_path)
        if not accounts:
            raise RuntimeError("No enabled TP Telephony Agent SIP identities are configured.")
        self.runtime = TelephonySipRuntime(
            accounts,
            call_log_writer=TelephonyCallLogWriter(site=self.site, bench_path=self.bench_path),
        )
        self.voice_server = TelephonyVoiceWebSocketServer(
            self.runtime,
            site=self.site,
            bench_path=self.bench_path,
            socket_path=voice_websocket_socket_path(site=self.site, bench_path=self.bench_path),
        )
        if self.install_signal_handlers and threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self.request_stop())
        try:
            self.runtime.start()
            self.voice_server.start()
            self._write_status("ready")
            self._next_account_reconcile_at = time.monotonic() + self.account_reconcile_interval
            print(f"TELEPHONY_RUNTIME_READY site={self.site} socket={self.voice_server.socket_path}", flush=True)
            while not self.stop_event.wait(1.0):
                self._reconcile_accounts_if_needed()
                self._write_status("ready")
        finally:
            self._write_status("stopping")
            if self.voice_server is not None:
                self.voice_server.stop()
            if self.runtime is not None:
                self.runtime.stop()
            self._remove_status()
            print(f"TELEPHONY_RUNTIME_STOPPED site={self.site}", flush=True)
        return 0

    def _reconcile_accounts_if_needed(self) -> None:
        if self.runtime is None:
            return
        now = time.monotonic()
        token = read_runtime_config_reload_token(site=self.site, bench_path=self.bench_path)
        reload_requested = token != self._account_reload_token
        periodic_due = now >= self._next_account_reconcile_at
        if not reload_requested and not periodic_due:
            return
        try:
            accounts = load_enabled_sip_accounts(site=self.site, bench_path=self.bench_path)
            changes = self.runtime.reconcile_accounts(accounts)
        except BaseException as exc:
            print(
                f"TELEPHONY_RUNTIME_ACCOUNT_RECONCILE_ERROR site={self.site} error={type(exc).__name__}",
                flush=True,
            )
            self._next_account_reconcile_at = now + min(5.0, self.account_reconcile_interval)
            return
        self._account_reload_token = token
        self._next_account_reconcile_at = now + self.account_reconcile_interval
        if any(changes.values()):
            print(
                "TELEPHONY_RUNTIME_ACCOUNTS_RECONCILED "
                f"site={self.site} added={len(changes['added'])} "
                f"changed={len(changes['changed'])} removed={len(changes['removed'])}",
                flush=True,
            )

    def _write_status(self, state: str) -> None:
        path = runtime_status_path(site=self.site, bench_path=self.bench_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "site": self.site,
            "pid": os.getpid(),
            "state": state,
            "started_at": self.started_at,
            "heartbeat_at": time.time(),
            "voice_socket": str(self.voice_server.socket_path) if self.voice_server else None,
            "voice_running": bool(self.voice_server and self.voice_server.running),
            "registrations": self.runtime.registration_snapshot() if self.runtime else {},
        }
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temp.replace(path)

    def _remove_status(self) -> None:
        runtime_status_path(site=self.site, bench_path=self.bench_path).unlink(missing_ok=True)


__all__ = ["TelephonyRuntimeProcess"]
