from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telephony.runtime.manager_state import (
    manager_status_is_fresh,
    read_manager_status,
    write_manager_status,
)
from telephony.runtime.watchdog import ensure_runtime_manager_for_bench


class _Child:
    def __init__(self, pid: int = 4321):
        self.pid = pid


class _Factory:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> _Child:
        self.commands.append(command)
        return _Child(pid=4321 + len(self.commands))


class RuntimeWatchdogTest(unittest.TestCase):
    def test_watchdog_launches_once_and_records_starting_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            factory = _Factory()
            self.assertTrue(
                ensure_runtime_manager_for_bench(
                    bench_path=bench,
                    process_factory=factory,
                    now=100.0,
                )
            )
            status = read_manager_status(bench_path=bench)
            self.assertEqual(status["state"], "starting")
            self.assertTrue(manager_status_is_fresh(status, now=100.0))
            self.assertEqual(len(factory.commands), 1)
            self.assertIn("telephony-runtime-manager", factory.commands[0])

            self.assertFalse(
                ensure_runtime_manager_for_bench(
                    bench_path=bench,
                    process_factory=factory,
                    now=101.0,
                )
            )
            self.assertEqual(len(factory.commands), 1)

    def test_watchdog_relaunches_when_manager_heartbeat_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            write_manager_status(
                bench_path=bench,
                state="ready",
                instance_id="old-manager",
                pid=111,
                started_at=10.0,
                heartbeat_at=10.0,
            )
            factory = _Factory()
            self.assertTrue(
                ensure_runtime_manager_for_bench(
                    bench_path=bench,
                    process_factory=factory,
                    now=100.0,
                )
            )
            self.assertEqual(len(factory.commands), 1)


if __name__ == "__main__":
    unittest.main()
