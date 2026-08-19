from __future__ import annotations

import fcntl
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

MANAGER_HEARTBEAT_INTERVAL = 5.0
MANAGER_STATUS_TTL = 30.0


def runtime_manager_dir(*, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / ".telephony-runtime"


def manager_status_path(*, bench_path: Path) -> Path:
    return runtime_manager_dir(bench_path=bench_path) / "manager-status.json"


def manager_lock_path(*, bench_path: Path) -> Path:
    return runtime_manager_dir(bench_path=bench_path) / "manager.lock"


def manager_start_lock_path(*, bench_path: Path) -> Path:
    return runtime_manager_dir(bench_path=bench_path) / "manager-start.lock"


def manager_log_path(*, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "logs" / "telephony-runtime-manager.log"


def _lock_file(path: Path, *, nonblocking: bool) -> IO[str] | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def acquire_manager_lock(*, bench_path: Path) -> IO[str] | None:
    return _lock_file(manager_lock_path(bench_path=bench_path), nonblocking=True)


@contextmanager
def manager_start_guard(*, bench_path: Path) -> Iterator[None]:
    handle = _lock_file(manager_start_lock_path(bench_path=bench_path), nonblocking=False)
    if handle is None:
        raise RuntimeError("Unable to acquire Telephony runtime manager start lock.")
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def release_manager_lock(handle: IO[str] | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def write_manager_status(
    *,
    bench_path: Path,
    state: str,
    instance_id: str,
    pid: int,
    started_at: float,
    heartbeat_at: float | None = None,
) -> None:
    path = manager_status_path(bench_path=bench_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "instance_id": instance_id,
        "pid": int(pid),
        "hostname": socket.gethostname(),
        "started_at": float(started_at),
        "heartbeat_at": float(heartbeat_at if heartbeat_at is not None else time.time()),
    }
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def read_manager_status(*, bench_path: Path) -> dict:
    path = manager_status_path(bench_path=bench_path)
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            return {}
        return status
    except (OSError, json.JSONDecodeError):
        return {}


def manager_status_is_fresh(status: dict, *, now: float | None = None, ttl: float = MANAGER_STATUS_TTL) -> bool:
    if status.get("state") not in {"starting", "ready"}:
        return False
    try:
        heartbeat_at = float(status.get("heartbeat_at") or 0)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else float(now)
    age = current - heartbeat_at
    return -5.0 <= age <= max(1.0, float(ttl))


def remove_manager_status(*, bench_path: Path, expected_instance_id: str) -> None:
    path = manager_status_path(bench_path=bench_path)
    status = read_manager_status(bench_path=bench_path)
    if status.get("instance_id") != expected_instance_id:
        return
    path.unlink(missing_ok=True)


__all__ = [
    "MANAGER_HEARTBEAT_INTERVAL",
    "MANAGER_STATUS_TTL",
    "acquire_manager_lock",
    "manager_log_path",
    "manager_start_guard",
    "manager_status_is_fresh",
    "manager_status_path",
    "read_manager_status",
    "release_manager_lock",
    "remove_manager_status",
    "runtime_manager_dir",
    "write_manager_status",
]
