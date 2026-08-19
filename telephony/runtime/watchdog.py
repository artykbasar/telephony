from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from telephony.runtime.manager import runtime_disabled
from telephony.runtime.manager_state import (
    manager_log_path,
    manager_start_guard,
    manager_status_is_fresh,
    read_manager_status,
    write_manager_status,
)


class ManagerChild(Protocol):
    pid: int


ProcessFactory = Callable[[list[str]], ManagerChild]


def _bench_binary(bench_path: Path) -> str:
    local = Path(bench_path).resolve() / "env" / "bin" / "bench"
    if local.exists():
        return str(local)
    return shutil.which("bench") or "bench"


def manager_command(*, bench_path: Path) -> list[str]:
    return [_bench_binary(bench_path), "telephony-runtime-manager"]


def _spawn_manager(*, bench_path: Path, command: list[str]) -> ManagerChild:
    log_path = manager_log_path(bench_path=bench_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    with log_path.open("ab", buffering=0) as output:
        return subprocess.Popen(
            command,
            cwd=Path(bench_path).resolve(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )


def ensure_runtime_manager_for_bench(
    *,
    bench_path: Path,
    process_factory: ProcessFactory | None = None,
    now: float | None = None,
) -> bool:
    bench_path = Path(bench_path).resolve()
    current = time.time() if now is None else float(now)
    # Scheduler hooks can race across sites/workers; serialize the check-and-spawn window.
    with manager_start_guard(bench_path=bench_path):
        if manager_status_is_fresh(read_manager_status(bench_path=bench_path), now=current):
            return False
        command = manager_command(bench_path=bench_path)
        child = process_factory(command) if process_factory else _spawn_manager(bench_path=bench_path, command=command)
        write_manager_status(
            bench_path=bench_path,
            state="starting",
            instance_id=f"launcher-{uuid.uuid4().hex}",
            pid=child.pid,
            started_at=current,
            heartbeat_at=current,
        )
        return True


def _site_requires_runtime() -> bool:
    import frappe

    if runtime_disabled(frappe.conf.get("telephony_runtime_disabled")):
        return False
    if not frappe.db.get_single_value("TP SIP Settings", "enabled"):
        return False
    return bool(frappe.db.exists("TP Telephony Agent", {"sip_enabled": 1}))


def ensure_runtime_manager() -> None:
    if not _site_requires_runtime():
        return
    from frappe.utils import get_bench_path

    ensure_runtime_manager_for_bench(bench_path=Path(get_bench_path()))


__all__ = ["ensure_runtime_manager", "ensure_runtime_manager_for_bench", "manager_command"]
