import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telephony.runtime.health import read_runtime_status
from telephony.runtime.socket_path import runtime_status_path


class RuntimeHealthTest(unittest.TestCase):
    def test_ready_requires_live_pid_voice_and_all_registrations(self):
        with tempfile.TemporaryDirectory() as temp:
            bench=Path(temp); site="a.test"; path=runtime_status_path(site=site, bench_path=bench); path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"site":site,"pid":os.getpid(),"state":"ready","voice_running":True,"registrations":{"alice":"registered","bob":"registered"}}))
            self.assertTrue(read_runtime_status(site=site, bench_path=bench)["ready"])
            path.write_text(json.dumps({"site":site,"pid":os.getpid(),"state":"ready","voice_running":True,"registrations":{"alice":"failed"}}))
            self.assertFalse(read_runtime_status(site=site, bench_path=bench)["ready"])

    def test_stale_pid_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            bench=Path(temp); site="a.test"; path=runtime_status_path(site=site, bench_path=bench); path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"site":site,"pid":99999999,"state":"ready","voice_running":True,"registrations":{"alice":"registered"}}))
            with patch("telephony.runtime.health.os.kill", side_effect=ProcessLookupError):
                self.assertEqual(read_runtime_status(site=site, bench_path=bench), {"ready": False})


if __name__ == "__main__": unittest.main()
