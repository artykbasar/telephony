from __future__ import annotations

import json
import os
from pathlib import Path

from telephony.runtime.socket_path import runtime_status_path


def read_runtime_status(*, site: str, bench_path: Path) -> dict:
    path = runtime_status_path(site=site, bench_path=bench_path)
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        pid = int(status.get("pid") or 0)
        if status.get("site") != site or pid <= 0:
            return {"ready": False}
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"ready": False}
    registrations = status.get("registrations") or {}
    ready = bool(status.get("state") == "ready" and status.get("voice_running") and registrations and all(value == "registered" for value in registrations.values()))
    return {**status, "ready": ready}


__all__ = ["read_runtime_status"]
