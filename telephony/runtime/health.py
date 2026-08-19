from __future__ import annotations

import json
import os
import time
from pathlib import Path

from telephony.runtime.socket_path import runtime_status_path

RUNTIME_STATUS_TTL = 5.0


def _runtime_process_is_live(status: dict, *, now: float | None = None) -> bool:
    try:
        heartbeat_at = float(status.get("heartbeat_at") or 0)
    except (TypeError, ValueError):
        heartbeat_at = 0
    if heartbeat_at > 0:
        # PIDs live in container namespaces, so a shared heartbeat is the portable liveness signal.
        current = time.time() if now is None else float(now)
        return -5.0 <= current - heartbeat_at <= RUNTIME_STATUS_TTL
    try:
        pid = int(status.get("pid") or 0)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_runtime_status(*, site: str, bench_path: Path, now: float | None = None) -> dict:
    path = runtime_status_path(site=site, bench_path=bench_path)
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        pid = int(status.get("pid") or 0)
        if status.get("site") != site or pid <= 0 or not _runtime_process_is_live(status, now=now):
            return {"ready": False}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"ready": False}
    registrations = status.get("registrations") or {}
    ready = bool(
        status.get("state") == "ready"
        and status.get("voice_running")
        and registrations
        and all(value == "registered" for value in registrations.values())
    )
    return {**status, "ready": ready}


__all__ = ["RUNTIME_STATUS_TTL", "read_runtime_status"]
