from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from telephony.local_https.config import local_https_status_path, load_local_https_runtime_config
from telephony.runtime.health import read_runtime_status


class RuntimeChild(Protocol):
    pid: int
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


SiteProvider = Callable[[], Sequence[str]]
ProcessFactory = Callable[[list[str]], RuntimeChild]


def runtime_disabled(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def site_runtime_disabled(*, site: str, bench_path: Path) -> bool:
    path = Path(bench_path).resolve() / "sites" / site / "site_config.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return runtime_disabled(values.get("telephony_runtime_disabled"))


def local_https_service_running(*, site: str, bench_path: Path) -> bool:
    path = local_https_status_path(site=site, bench_path=bench_path)
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        pid = int(status.get("pid") or 0)
        if status.get("site") != site or pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


def runtime_service_running(*, site: str, bench_path: Path) -> bool:
    status = read_runtime_status(site=site, bench_path=bench_path)
    return int(status.get("pid") or 0) > 0


class TelephonyRuntimeManager:
    def __init__(self, *, bench_path: Path, site_provider: SiteProvider, process_factory: ProcessFactory | None = None, poll_interval: float = 5.0, site_discovery_interval: float = 60.0, restart_backoff: float = 2.0, shutdown_timeout: float = 8.0) -> None:
        self.bench_path = Path(bench_path).resolve()
        self.site_provider = site_provider
        self.process_factory = process_factory or self._spawn
        self.poll_interval = max(0.1, float(poll_interval))
        self.site_discovery_interval = max(0.0, float(site_discovery_interval))
        self.restart_backoff = max(0.0, float(restart_backoff))
        self.shutdown_timeout = max(0.1, float(shutdown_timeout))
        self.stop_event = threading.Event()
        self.children: dict[str, RuntimeChild] = {}
        self.local_https_children: dict[str, RuntimeChild] = {}
        self._restart_after: dict[str, float] = {}
        self._local_https_restart_after: dict[str, float] = {}
        self._desired_sites: set[str] = set()
        self._next_site_discovery_at = 0.0

    def run(self, *, install_signal_handlers: bool = True) -> int:
        if install_signal_handlers and threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self.request_stop())
        print(f"TELEPHONY_RUNTIME_MANAGER_READY bench={self.bench_path}", flush=True)
        try:
            while not self.stop_event.is_set():
                self.reconcile_once()
                self.stop_event.wait(self.poll_interval)
        finally:
            self.stop_all()
            print("TELEPHONY_RUNTIME_MANAGER_STOPPED", flush=True)
        return 0

    def request_stop(self) -> None:
        self.stop_event.set()

    def reconcile_once(self) -> None:
        now = time.monotonic()
        if now >= self._next_site_discovery_at:
            self._desired_sites = {site for site in self.site_provider() if site}
            self._next_site_discovery_at = now + self.site_discovery_interval
        for site in sorted(set(self.children) - self._desired_sites):
            self._stop_site(site)
        for site in sorted(set(self.local_https_children) - self._desired_sites):
            self._stop_local_https_site(site)
        for site in sorted(self._desired_sites):
            child = self.children.get(site)
            if child is None:
                if not runtime_service_running(site=site, bench_path=self.bench_path):
                    if now >= self._restart_after.get(site, 0.0):
                        self._start_site(site)
            elif child.poll() is not None:
                self.children.pop(site, None)
                self._restart_after[site] = now + self.restart_backoff
                if not runtime_service_running(site=site, bench_path=self.bench_path):
                    if now >= self._restart_after.get(site, 0.0):
                        self._start_site(site)
            self._reconcile_local_https_site(site, now)

    def _reconcile_local_https_site(self, site: str, now: float) -> None:
        config = load_local_https_runtime_config(site=site, bench_path=self.bench_path)
        child = self.local_https_children.get(site)
        if not config.enabled:
            if child is not None:
                self._stop_local_https_site(site)
            return
        if child is not None and child.poll() is None:
            return
        if child is not None:
            self.local_https_children.pop(site, None)
            self._local_https_restart_after[site] = now + self.restart_backoff
        if local_https_service_running(site=site, bench_path=self.bench_path):
            return
        if now >= self._local_https_restart_after.get(site, 0.0):
            child = self.process_factory(self.local_https_child_command(site))
            self.local_https_children[site] = child
            self._local_https_restart_after.pop(site, None)
            print(f"TELEPHONY_LOCAL_HTTPS_MANAGER_START site={site} pid={child.pid}", flush=True)

    def _start_site(self, site: str) -> None:
        child = self.process_factory(self.child_command(site))
        self.children[site] = child
        self._restart_after.pop(site, None)
        print(f"TELEPHONY_RUNTIME_MANAGER_START site={site} pid={child.pid}", flush=True)

    def _stop_site(self, site: str) -> None:
        child = self.children.pop(site, None)
        self._restart_after.pop(site, None)
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=self.shutdown_timeout)

    def _stop_local_https_site(self, site: str) -> None:
        child = self.local_https_children.pop(site, None)
        self._local_https_restart_after.pop(site, None)
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=self.shutdown_timeout)

    def stop_all(self) -> None:
        for site in list(self.local_https_children):
            self._stop_local_https_site(site)
        for site in list(self.children):
            self._stop_site(site)

    def child_command(self, site: str) -> list[str]:
        return [shutil.which("bench") or "bench", "--site", site, "telephony-runtime"]

    def local_https_child_command(self, site: str) -> list[str]:
        return [shutil.which("bench") or "bench", "--site", site, "telephony-local-https"]

    def _spawn(self, command: list[str]) -> RuntimeChild:
        return subprocess.Popen(command, cwd=self.bench_path, env=os.environ.copy())


def discover_telephony_sites(bench_path: Path) -> list[str]:
    import frappe
    from frappe.utils import get_sites

    sites_path = Path(bench_path).resolve() / "sites"
    result = []
    for site in get_sites(str(sites_path)):
        try:
            frappe.init(site=site, sites_path=str(sites_path))
            frappe.connect()
            if "telephony" not in frappe.get_installed_apps():
                continue
            if runtime_disabled(frappe.conf.get("telephony_runtime_disabled")):
                continue
            if not frappe.db.get_single_value("TP SIP Settings", "enabled"):
                continue
            if not frappe.db.exists("TP Telephony Agent", {"sip_enabled": 1}):
                continue
            result.append(site)
        except Exception as exc:
            print(f"TELEPHONY_RUNTIME_MANAGER_SITE_ERROR site={site} error={type(exc).__name__}", flush=True)
        finally:
            frappe.destroy()
    return result


__all__ = ["TelephonyRuntimeManager", "discover_telephony_sites", "local_https_service_running", "runtime_disabled", "site_runtime_disabled"]
