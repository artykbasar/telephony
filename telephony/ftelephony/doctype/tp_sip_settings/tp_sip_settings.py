from __future__ import annotations

import ipaddress
import json
import os
import time
from pathlib import Path

import frappe
from frappe import _
from frappe.model.document import Document

from telephony.local_https.config import format_https_url, normalise_local_https_host


class TPSIPSettings(Document):
    def validate(self):
        self._validate_local_https()
        if not self.enabled:
            return
        if not (self.sip_server or "").strip():
            frappe.throw(_("SIP Server is required when SIP is enabled"))
        port = int(self.sip_port or 5060)
        if not 1 <= port <= 65535:
            frappe.throw(_("SIP Port must be between 1 and 65535"))
        transport = (self.transport or "UDP").upper()
        if transport not in {"UDP", "TCP", "TLS"}:
            frappe.throw(_("Transport must be UDP, TCP, or TLS"))
        bind = (self.local_bind_address or "0.0.0.0").strip()
        try:
            ipaddress.ip_address(bind)
        except ValueError:
            frappe.throw(_("Local Bind Address must be an IP address"))
        if self.proxy_port and not 1 <= int(self.proxy_port) <= 65535:
            frappe.throw(_("Proxy Port must be between 1 and 65535"))

    def on_update(self):
        sync_local_https_runtime_config(self)

    def _validate_local_https(self):
        port = int(self.local_https_port or 8443)
        if port < 1024 or port > 65535:
            frappe.throw(_("Local HTTPS Port must be between 1024 and 65535."))
        self.local_https_port = port
        host = str(self.local_https_host or "").strip()
        if not host and not self.enable_local_https:
            return
        try:
            self.local_https_host = normalise_local_https_host(host)
        except ValueError as exc:
            frappe.throw(_(str(exc)))


def _local_https_directory() -> Path:
    return Path(frappe.get_site_path("private", "telephony", "local_https"))


def sync_local_https_runtime_config(settings: TPSIPSettings) -> dict:
    directory = _local_https_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    host = str(settings.local_https_host or "").strip()
    bind_address = "0.0.0.0"
    try:
        if ipaddress.ip_address(host).version == 6:
            bind_address = "::"
    except ValueError:
        pass
    payload = {
        "enabled": bool(settings.enable_local_https),
        "host": host,
        "port": int(settings.local_https_port or 8443),
        "bind_address": bind_address,
        "updated_at": time.time(),
    }
    path = directory / "config.json"
    temporary = directory / f".config.json.{os.getpid()}.tmp"
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def _assert_settings_access() -> None:
    if not frappe.has_permission("TP SIP Settings", ptype="read"):
        frappe.throw(_("Not permitted to read TP SIP Settings."), frappe.PermissionError)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_is_alive(value: object) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


@frappe.whitelist()
def get_local_https_status() -> dict:
    _assert_settings_access()
    settings = frappe.get_single("TP SIP Settings")
    enabled = bool(settings.enable_local_https)
    host = str(settings.local_https_host or "").strip()
    port = int(settings.local_https_port or 8443)
    url = format_https_url(host, port) if host else None
    directory = _local_https_directory()
    status = _read_json(directory / "status.json")

    process_alive = _pid_is_alive(status.get("pid"))
    status_ready = status.get("state") == "ready" and process_alive
    same_endpoint = status.get("host") == host and int(status.get("port") or 0) == port
    active_url = str(status.get("url") or "") if status_ready else None

    state = "disabled"
    message = _("Local HTTPS testing is disabled.")
    if not enabled and status_ready:
        state = "stopping"
        message = _("Stopping the previous local HTTPS endpoint.")
    elif enabled:
        state = "starting"
        message = _("Starting the configured local HTTPS endpoint.")
        if same_endpoint and status_ready:
            state = "ready"
            message = _("Local HTTPS testing endpoint is ready.")
        elif status_ready:
            state = "switching"
            message = _("Switching from the previous local HTTPS endpoint to the configured address and port.")
        elif same_endpoint and status.get("state") == "error":
            state = "error"
            message = str(status.get("message") or _("Local HTTPS failed to start."))

    return {
        "enabled": enabled,
        "state": state,
        "message": message,
        "host": host,
        "port": port,
        "url": url,
        "active_url": active_url,
        "certificate": "self-signed" if (directory / "cert.pem").exists() else "pending",
        "certificate_warning_expected": enabled,
    }
