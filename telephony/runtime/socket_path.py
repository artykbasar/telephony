from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

_MAX_SAFE_UNIX_SOCKET_PATH = 100


def _temporary_runtime_base() -> Path:
    return Path(tempfile.gettempdir()) / f"frappe-telephony-{os.getuid()}"


def _configured_runtime_base(*, environment: Mapping[str, str]) -> Path | None:
    configured = str(environment.get("TELEPHONY_RUNTIME_DIR") or "").strip()
    return Path(configured) if configured else None


def _shared_runtime_base(*, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / ".telephony-runtime"


def _socket_key(*, site: str, bench_path: Path) -> str:
    bench_key = hashlib.sha256(str(Path(bench_path).resolve()).encode()).hexdigest()[:12]
    site_key = hashlib.sha256(site.encode()).hexdigest()[:12]
    return f"{bench_key}-{site_key}"


def voice_websocket_socket_path(
    *,
    site: str,
    bench_path: Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    source = os.environ if environment is None else environment
    filename = f"{_socket_key(site=site, bench_path=bench_path)}-voice.sock"
    configured = _configured_runtime_base(environment=source)
    if configured is not None:
        return configured / filename
    # Production Frappe commonly splits workers and Socket.IO into separate containers.
    # Keep the Unix socket on the shared sites volume when the path fits the OS limit.
    shared = _shared_runtime_base(bench_path=bench_path) / filename
    if len(os.fsencode(str(shared))) < _MAX_SAFE_UNIX_SOCKET_PATH:
        return shared
    # Long development bench paths (especially on macOS) exceed AF_UNIX limits.
    return _temporary_runtime_base() / filename


def runtime_status_path(*, site: str, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / site / "private" / "telephony" / "runtime-status.json"


__all__ = ["runtime_status_path", "voice_websocket_socket_path"]
