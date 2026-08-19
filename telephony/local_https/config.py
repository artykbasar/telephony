from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")


def local_https_directory(*, site: str, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / site / "private" / "telephony" / "local_https"


def local_https_config_path(*, site: str, bench_path: Path) -> Path:
    return local_https_directory(site=site, bench_path=bench_path) / "config.json"


def local_https_status_path(*, site: str, bench_path: Path) -> Path:
    return local_https_directory(site=site, bench_path=bench_path) / "status.json"


def normalise_local_https_host(value: object) -> str:
    host = str(value or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1].strip()
    if not host:
        raise ValueError("Local HTTPS host is required.")
    if "://" in host or "/" in host or "?" in host or "#" in host:
        raise ValueError("Local HTTPS host must be a hostname or IP address, not a URL.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        host = host.rstrip(".").lower()
        if len(host) > 253 or not _HOST_RE.fullmatch(host):
            raise ValueError("Local HTTPS host must be a valid hostname or IP address.")
        return host
    if address.is_unspecified:
        raise ValueError("Local HTTPS host cannot be a wildcard address.")
    return address.compressed


def format_https_url(host: str, port: int) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        display_host = host
    else:
        display_host = f"[{host}]" if address.version == 6 else host
    return f"https://{display_host}:{int(port)}"


@dataclass(frozen=True)
class LocalHttpsRuntimeConfig:
    enabled: bool = False
    host: str = ""
    port: int = 8443
    bind_address: str = "0.0.0.0"

    @property
    def url(self) -> str | None:
        return format_https_url(self.host, self.port) if self.enabled and self.host else None

    @property
    def signature(self) -> str:
        return json.dumps(
            {
                "enabled": self.enabled,
                "host": self.host,
                "port": self.port,
                "bind_address": self.bind_address,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def load_local_https_runtime_config(*, site: str, bench_path: Path) -> LocalHttpsRuntimeConfig:
    path = local_https_config_path(site=site, bench_path=bench_path)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalHttpsRuntimeConfig()
    try:
        host = normalise_local_https_host(values.get("host")) if values.get("enabled") else ""
        port = int(values.get("port") or 8443)
        bind_address = str(values.get("bind_address") or "0.0.0.0").strip()
        ipaddress.ip_address(bind_address)
    except (TypeError, ValueError):
        return LocalHttpsRuntimeConfig()
    if port < 1024 or port > 65535:
        return LocalHttpsRuntimeConfig()
    return LocalHttpsRuntimeConfig(
        enabled=bool(values.get("enabled")),
        host=host,
        port=port,
        bind_address=bind_address,
    )
