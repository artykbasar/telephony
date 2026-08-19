from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def _runtime_base(*, environment: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environment is None else environment
    configured = str(source.get("TELEPHONY_RUNTIME_DIR") or "").strip()
    return Path(configured) if configured else Path(tempfile.gettempdir()) / f"frappe-telephony-{os.getuid()}"


def _socket_key(*, site: str, bench_path: Path) -> str:
    bench_key = hashlib.sha256(str(Path(bench_path).resolve()).encode()).hexdigest()[:12]
    site_key = hashlib.sha256(site.encode()).hexdigest()[:12]
    return f"{bench_key}-{site_key}"


def voice_websocket_socket_path(*, site: str, bench_path: Path, environment: Mapping[str, str] | None = None) -> Path:
    return _runtime_base(environment=environment) / f"{_socket_key(site=site, bench_path=bench_path)}-voice.sock"


def runtime_status_path(*, site: str, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / site / "private" / "telephony" / "runtime-status.json"


__all__ = ["runtime_status_path", "voice_websocket_socket_path"]
