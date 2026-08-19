from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from telephony.runtime.manager import TelephonyRuntimeManager, runtime_disabled, site_runtime_disabled
from telephony.runtime.manager_state import read_manager_status


class _Child:
    next_pid = 2000
    def __init__(self, command):
        self.command = command; self.pid = _Child.next_pid; _Child.next_pid += 1
        self.exit_code = None; self.terminated = False; self.killed = False
    def poll(self): return self.exit_code
    def terminate(self): self.terminated = True; self.exit_code = 0
    def wait(self, timeout=None): return self.exit_code or 0
    def kill(self): self.killed = True; self.exit_code = -9


class _Factory:
    def __init__(self): self.children = []
    def __call__(self, command):
        child = _Child(command); self.children.append(child); return child


class TelephonyRuntimeManagerTest(unittest.TestCase):
    def test_runtime_disabled_flag(self):
        for value in (1, True, "1", "true", "YES", "on"):
            self.assertTrue(runtime_disabled(value))
        for value in (0, False, None, "", "0", "false", "off"):
            self.assertFalse(runtime_disabled(value))

    def test_site_runtime_disabled_is_telephony_namespaced(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp); site = bench / "sites" / "a.test"; site.mkdir(parents=True)
            (site / "site_config.json").write_text('{"telephony_runtime_disabled": 1, "telecom_runtime_disabled": 0}')
            self.assertTrue(site_runtime_disabled(site="a.test", bench_path=bench))

    def test_manager_supervises_local_https_when_site_config_enables_it(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp); site_name = "a.test"
            config = bench / "sites" / site_name / "private" / "telephony" / "local_https" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"enabled": True, "host": "127.0.0.1", "port": 8443, "bind_address": "127.0.0.1"}))
            factory = _Factory()
            manager = TelephonyRuntimeManager(bench_path=bench, site_provider=lambda: (site_name,), process_factory=factory, site_discovery_interval=0, restart_backoff=0)
            manager.reconcile_once()
            self.assertIn(site_name, manager.local_https_children)
            https_child = manager.local_https_children[site_name]
            self.assertIn("telephony-local-https", https_child.command)
            manager.reconcile_once()
            self.assertIs(manager.local_https_children[site_name], https_child)
            config.write_text(json.dumps({"enabled": False}))
            manager.reconcile_once()
            self.assertTrue(https_child.terminated)
            self.assertNotIn(site_name, manager.local_https_children)

    def test_manager_does_not_duplicate_live_runtime_after_manager_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp); site_name = "a.test"
            status = bench / "sites" / site_name / "private" / "telephony" / "runtime-status.json"
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({
                "site": site_name, "pid": os.getpid(), "state": "ready",
                "voice_running": True, "registrations": {"agent": "registered"},
            }))
            factory = _Factory()
            manager = TelephonyRuntimeManager(bench_path=bench, site_provider=lambda: (site_name,), process_factory=factory, site_discovery_interval=0, restart_backoff=0)
            manager.reconcile_once()
            self.assertEqual(factory.children, [])
            status.unlink()
            manager.reconcile_once()
            self.assertIn(site_name, manager.children)

    def test_manager_starts_restarts_and_removes_site_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            desired = ["a.test", "b.test"]; factory = _Factory()
            manager = TelephonyRuntimeManager(bench_path=Path(temp), site_provider=lambda: tuple(desired), process_factory=factory, site_discovery_interval=0, restart_backoff=0)
            manager.reconcile_once()
            self.assertEqual(set(manager.children), set(desired))
            self.assertIn("telephony-runtime", factory.children[0].command)
            old = manager.children["a.test"]; old.exit_code = 3
            manager.reconcile_once()
            self.assertIsNot(manager.children["a.test"], old)
            removed = manager.children["b.test"]; desired.remove("b.test"); manager.reconcile_once()
            self.assertTrue(removed.terminated)
            self.assertNotIn("b.test", manager.children)

    def test_manager_heartbeat_and_lifetime_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            bench = Path(temp)
            manager = TelephonyRuntimeManager(
                bench_path=bench,
                site_provider=lambda: (),
                poll_interval=0.01,
                site_discovery_interval=0,
                heartbeat_interval=0.01,
            )
            thread = threading.Thread(
                target=lambda: manager.run(install_signal_handlers=False),
                daemon=True,
            )
            thread.start()
            deadline = time.time() + 1.0
            while time.time() < deadline:
                status = read_manager_status(bench_path=bench)
                if status.get("state") == "ready":
                    break
                time.sleep(0.01)
            else:
                self.fail("runtime manager did not publish a ready heartbeat")

            duplicate = TelephonyRuntimeManager(bench_path=bench, site_provider=lambda: ())
            self.assertEqual(duplicate.run(install_signal_handlers=False), 0)
            self.assertTrue(thread.is_alive())

            manager.request_stop()
            thread.join(timeout=1.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(read_manager_status(bench_path=bench), {})


if __name__ == "__main__": unittest.main()
