from __future__ import annotations

import os
import time
from pathlib import Path


_RELOAD_FILENAME = "runtime-config.reload"


def runtime_config_reload_path(*, site: str, bench_path: Path) -> Path:
    return Path(bench_path).resolve() / "sites" / site / "private" / "telephony" / _RELOAD_FILENAME


def read_runtime_config_reload_token(*, site: str, bench_path: Path) -> str:
    path = runtime_config_reload_path(site=site, bench_path=bench_path)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_runtime_config_reload_token(path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    token = f"{time.time_ns()}-{os.getpid()}"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(token, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return token


def signal_runtime_config_reload() -> str:
    import frappe

    path = Path(frappe.get_site_path("private", "telephony", _RELOAD_FILENAME))
    return write_runtime_config_reload_token(path)


def schedule_runtime_config_reload() -> None:
    import frappe

    after_commit = getattr(frappe.db, "after_commit", None)
    if after_commit is not None and hasattr(after_commit, "add"):
        after_commit.add(signal_runtime_config_reload)
        return
    signal_runtime_config_reload()


__all__ = [
    "read_runtime_config_reload_token",
    "runtime_config_reload_path",
    "schedule_runtime_config_reload",
    "signal_runtime_config_reload",
    "write_runtime_config_reload_token",
]
