from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import websockets

from telephony.voice.sip import SipCallState, SipIncomingCall
from telephony.voice.wss.auth import create_ticket
from telephony.voice.wss.server import TelephonyVoiceWebSocketServer, _VoiceClient


class _Bridge:
    def __init__(self, call_id):
        self.call_id = call_id
        self.running = True
        self.frame_size = 640
        self.audio_format = SimpleNamespace(sample_rate=16000, channels=1, bit_depth=16, frame_ms=20)
        self.chunks = []

    def push_browser_audio(self, chunk): self.chunks.append(bytes(chunk))
    def set_held(self, held): pass
    def status(self):
        return {
            "running": self.running,
            "browser_queue_max": 10, "browser_queue_xoff": 9, "browser_queue_xon": 2,
            "browser_frames_received": len(self.chunks), "browser_frames_dropped": 0,
            "sip_frames_written": len(self.chunks), "sip_frames_read": 0, "sip_frames_dropped": 0,
            "browser_frames_sent": 0, "sip_null_ticks": 0,
            "flow_xoff_events": 0, "flow_xon_events": 0,
        }


class _Runtime:
    def __init__(self):
        self.incoming = []
        self.states = []
        self.calls = {}
        self.actions = []
        self.sessions = {}
        self.session_tabs = {}
        self.leases = {}

    def add_incoming_listener(self, callback): self.incoming.append(callback)
    def add_state_listener(self, callback): self.states.append(callback)
    def registration_snapshot(self): return {"agent@example.test": "registered"}
    def active_call_snapshots(self, *, user):
        return [dict(value, call_id=call_id) for call_id, value in getattr(self, "snapshots", {}).items() if value.get("user") == user]
    def voice_client_connected(self, user, session_id, *, tab_id=None):
        self.sessions[session_id] = user
        if tab_id: self.session_tabs[session_id] = tab_id
    def voice_client_disconnected(self, user, session_id):
        self.sessions.pop(session_id, None)
        self.session_tabs.pop(session_id, None)
        orphaned = []
        for call_id, lease in self.leases.items():
            if lease.get("user") == user and lease.get("session_id") == session_id:
                lease["session_id"] = None
                lease["orphaned"] = True
                orphaned.append(call_id)
        return orphaned
    def voice_tab_restore_pending(self, *, user, session_id, tab_id):
        if not tab_id: return False
        return any(
            lease.get("user") == user and lease.get("tab_id") == tab_id
            and lease.get("session_id") not in {None, session_id}
            for lease in self.leases.values()
        )
    def voice_restore_handsets(self, *, user, session_id, tab_id):
        restored = []
        if self.sessions.get(session_id) != user or not tab_id: return restored
        for call_id, lease in self.leases.items():
            if lease.get("user") == user and lease.get("session_id") is None and lease.get("tab_id") == tab_id:
                lease["session_id"] = session_id
                lease["orphaned"] = False
                restored.append(call_id)
        return restored
    def voice_handset_state(self, call_id, *, user, session_id):
        lease = self.leases.get(call_id)
        if lease is None: return {"status":"available","owner":False,"can_claim":True,"grace_seconds":10.0}
        if lease.get("user") != user: return {"status":"unavailable","owner":False,"can_claim":False,"grace_seconds":10.0}
        if lease.get("session_id") == session_id: return {"status":"owner","owner":True,"can_claim":False,"grace_seconds":10.0}
        if lease.get("session_id") is None: return {"status":"orphaned","owner":False,"can_claim":True,"grace_seconds":10.0}
        return {"status":"observer","owner":False,"can_claim":False,"grace_seconds":10.0}
    def voice_claim_handset(self, call_id, *, user, session_id):
        if self.sessions.get(session_id) != user: raise RuntimeError("session missing")
        lease = self.leases.get(call_id)
        if lease and lease.get("session_id") not in {None, session_id}:
            return self.voice_handset_state(call_id, user=user, session_id=session_id)
        self.leases[call_id] = {"user": user, "session_id": session_id, "tab_id": self.session_tabs.get(session_id)}
        return self.voice_handset_state(call_id, user=user, session_id=session_id)
    def voice_handset_is_owner(self, call_id, *, user, session_id):
        return self.voice_handset_state(call_id, user=user, session_id=session_id).get("owner", False)
    def dial(self, *, user, number):
        call_id = f"call-{len(self.calls)+1}"
        self.calls[call_id] = SimpleNamespace(user=user)
        self.actions.append(("dial", user, number))
        return call_id
    def account_for_call(self, call_id):
        if call_id not in self.calls:
            raise RuntimeError("missing call")
        return self.calls[call_id]
    def hold(self, *, user, call_id): self.actions.append(("hold", user, call_id))
    def resume(self, *, user, call_id): self.actions.append(("resume", user, call_id))
    def hangup(self, *, user, call_id): self.actions.append(("hangup", user, call_id))
    def answer(self, *, user, call_id): self.actions.append(("answer", user, call_id))
    def reject(self, *, user, call_id): self.actions.append(("reject", user, call_id))
    def dtmf(self, *, user, call_id, digit): self.actions.append(("dtmf", user, call_id, digit)); return True
    def transfer(self, **kwargs): self.actions.append(("transfer", kwargs)); return {"completed": True}
    def attended_transfer(self, **kwargs): self.actions.append(("attended", kwargs)); return {"completed": True}
    def start_media(self, *, user, session_id, call_id, send_to_browser, **kwargs):
        bridge = _Bridge(call_id)
        self.actions.append(("media_start", user, call_id, bridge))
        return bridge
    def call_media_status(self, call_id, bridge=None):
        result = {"call_id": call_id, "engine": "FakeEngine", "sip": {"codec": "OPUS", "rtp_streams": 1}}
        if bridge is not None:
            result["bridge"] = bridge.status()
        return result
    def stop_media(self, bridge):
        if bridge is not None:
            bridge.running = False
            self.actions.append(("media_stop", bridge.call_id))


class VoiceWebSocketServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bench_path = Path(self.temp.name)
        self.site = "voice.test"
        site_path = self.bench_path / "sites" / self.site
        site_path.mkdir(parents=True)
        (site_path / "site_config.json").write_text(json.dumps({"encryption_key": "test-secret-key"}))
        self.runtime = _Runtime()
        self.socket_path = self.bench_path / "telephony-voice.sock"
        self.server = TelephonyVoiceWebSocketServer(
            self.runtime, site=self.site, bench_path=self.bench_path, socket_path=self.socket_path
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.temp.cleanup()

    async def _connect(self, *, tab_id=None):
        socket = await websockets.unix_connect(str(self.socket_path), uri="ws://localhost/telephony-voice", compression=None)
        ticket = create_ticket(site=self.site, user="agent@example.test", bench_path=self.bench_path)
        auth = {"type": "auth", "ticket": ticket, "protocol_version": 2}
        if tab_id:
            auth["tab_id"] = tab_id
        await socket.send(json.dumps(auth))
        ready = json.loads(await socket.recv())
        return socket, ready

    def test_incoming_event_carries_caller_name_and_ivr_route(self):
        async def run():
            ws, _ = await self._connect()
            try:
                account = SimpleNamespace(user="agent@example.test", extension="2001")
                self.runtime.incoming[0](
                    account,
                    SipIncomingCall("incoming-meta", "447700900123", "2001", "John Smith", "Landlord"),
                )
                message = json.loads(await ws.recv())
                self.assertEqual(message["type"], "call.incoming")
                self.assertEqual(message["call"]["caller_id"], "447700900123")
                self.assertEqual(message["call"]["caller_name"], "John Smith")
                self.assertEqual(message["call"]["ivr_route"], "Landlord")
            finally:
                await ws.close()
        asyncio.run(run())

    def test_answer_claims_handset_before_connected_state(self):
        async def run():
            ws, _ = await self._connect(tab_id="answer-tab")
            try:
                call_id = "incoming-answer"
                account = SimpleNamespace(user="agent@example.test", extension="2001")
                self.runtime.calls[call_id] = account
                await ws.send(json.dumps({"type": "call.answer", "call_id": call_id}))
                handset = json.loads(await ws.recv())
                self.assertEqual(handset["type"], "call.handset")
                self.assertEqual(handset["call_id"], call_id)
                self.assertEqual(handset["handset"]["status"], "owner")
                self.assertTrue(handset["handset"]["owner"])
                self.assertIn(("answer", "agent@example.test", call_id), self.runtime.actions)

                self.runtime.states[0](account, call_id, SipCallState.CONNECTED)
                state = json.loads(await ws.recv())
                self.assertEqual(state["type"], "call.state")
                self.assertEqual(state["state"]["state"], "connected")
                self.assertEqual(state["state"]["handset"]["status"], "owner")
                self.assertTrue(state["state"]["handset"]["owner"])
            finally:
                await ws.close()
        asyncio.run(run())

    def test_second_server_cannot_unlink_active_voice_socket(self):
        occupied_path = self.bench_path / "occupied-voice.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(occupied_path))
            listener.listen(1)
            inode = occupied_path.stat().st_ino
            second = TelephonyVoiceWebSocketServer(
                _Runtime(), site=self.site, bench_path=self.bench_path, socket_path=occupied_path
            )
            with self.assertRaisesRegex(RuntimeError, "already active"):
                second.start()
            self.assertTrue(occupied_path.exists())
            self.assertEqual(occupied_path.stat().st_ino, inode)
        finally:
            listener.close()
            occupied_path.unlink(missing_ok=True)

    def test_old_server_cleanup_does_not_unlink_replacement_socket(self):
        old_inode = self.socket_path.stat().st_ino
        self.assertEqual(self.server._socket_inode, old_inode)
        self.socket_path.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            replacement.bind(str(self.socket_path))
            replacement_inode = self.socket_path.stat().st_ino
            self.assertNotEqual(replacement_inode, old_inode)
            self.server._unlink_owned_socket()
            self.assertTrue(self.socket_path.exists())
            self.assertEqual(self.socket_path.stat().st_ino, replacement_inode)
        finally:
            replacement.close()
            self.socket_path.unlink(missing_ok=True)

    def test_full_media_queue_discards_pcm_before_control_messages(self):
        async def run():
            isolated = TelephonyVoiceWebSocketServer(
                _Runtime(), site=self.site, bench_path=self.bench_path, socket_path=self.bench_path / "unused.sock"
            )
            isolated._loop = asyncio.get_running_loop()
            client = _VoiceClient(SimpleNamespace(), "agent@example.test", "session")
            for _ in range(client.outbound.maxsize):
                client.outbound.put_nowait(b"pcm")
            isolated._enqueue(client, json.dumps({"type": "control"}))
            await asyncio.sleep(0)
            queued = []
            while not client.outbound.empty():
                queued.append(client.outbound.get_nowait())
            self.assertEqual(queued, [json.dumps({"type": "control"})])
            self.assertEqual(client.outbound_media_dropped, 24)
        asyncio.run(run())

    def test_media_frame_without_attached_bridge_is_dropped_without_user_error(self):
        async def run():
            isolated = TelephonyVoiceWebSocketServer(
                _Runtime(), site=self.site, bench_path=self.bench_path, socket_path=self.bench_path / "unused.sock"
            )
            isolated._loop = asyncio.get_running_loop()
            client = _VoiceClient(SimpleNamespace(), "agent@example.test", "session")
            await isolated._handle_media(client, b"pcm")
            self.assertEqual(client.inbound_media_dropped, 1)
            self.assertTrue(client.outbound.empty())
        asyncio.run(run())

    def test_stale_media_stop_cannot_tear_down_newer_call_bridge(self):
        async def run():
            ws, _ = await self._connect(tab_id="media-tab")
            try:
                await ws.send(json.dumps({"type": "call.dial", "number": "3000"}))
                first_call = json.loads(await ws.recv())["call"]["call_id"]
                await ws.send(json.dumps({"type": "media.start", "call_id": first_call}))
                first_ready = json.loads(await ws.recv())
                self.assertEqual(first_ready["type"], "media.ready")
                self.assertEqual(first_ready["queue_max_frames"], 10)

                await ws.send(json.dumps({"type": "call.dial", "number": "3001"}))
                second_call = json.loads(await ws.recv())["call"]["call_id"]
                await ws.send(json.dumps({"type": "media.start", "call_id": second_call}))
                self.assertEqual(json.loads(await ws.recv())["type"], "media.ready")

                await ws.send(json.dumps({"type": "media.stop", "call_id": first_call}))
                stopped = json.loads(await ws.recv())
                self.assertEqual(stopped["type"], "media.stopped")
                self.assertEqual(stopped["call_id"], first_call)

                await ws.send(json.dumps({"type": "media.status", "call_id": second_call}))
                status = json.loads(await ws.recv())
                self.assertEqual(status["type"], "media.status")
                self.assertTrue(status["status"]["running"])
                self.assertEqual([a[:2] for a in self.runtime.actions if a[0] == "media_stop"], [("media_stop", first_call)])
            finally:
                await ws.close()
        asyncio.run(run())

    def test_auth_ping_and_native_session_capabilities(self):
        async def run():
            socket, ready = await self._connect(tab_id="tab-a")
            try:
                self.assertEqual(ready["type"], "session.ready")
                self.assertEqual(ready["registration"]["agent@example.test"], "registered")
                self.assertTrue(ready["media"]["binary_media"])
                await socket.send(json.dumps({"type": "ping", "nonce": "n1"}))
                pong = json.loads(await socket.recv())
                self.assertEqual(pong["type"], "pong")
                self.assertEqual(pong["nonce"], "n1")
            finally:
                await socket.close()
        asyncio.run(run())

    def test_dial_never_accepts_sip_credentials_and_owner_controls_call(self):
        async def run():
            socket, _ = await self._connect(tab_id="tab-a")
            try:
                await socket.send(json.dumps({"type": "call.dial", "number": "441234", "sip_password": "must-be-ignored"}))
                created = json.loads(await socket.recv())
                call_id = created["call"]["call_id"]
                self.assertEqual(self.runtime.actions[0], ("dial", "agent@example.test", "441234"))
                await socket.send(json.dumps({"type": "call.hold", "call_id": call_id}))
                await asyncio.sleep(0.05)
                self.assertIn(("hold", "agent@example.test", call_id), self.runtime.actions)
            finally:
                await socket.close()
        asyncio.run(run())

    def test_second_tab_cannot_control_first_tabs_call(self):
        async def run():
            first, _ = await self._connect(tab_id="tab-a")
            second, _ = await self._connect(tab_id="tab-b")
            try:
                await first.send(json.dumps({"type": "call.dial", "number": "3000"}))
                created = json.loads(await first.recv())
                call_id = created["call"]["call_id"]
                observed = json.loads(await second.recv())
                self.assertEqual(observed["type"], "call.created")
                self.assertEqual(observed["call"]["call_id"], call_id)
                self.assertEqual(observed["call"]["handset"]["status"], "observer")
                await second.send(json.dumps({"type": "call.hold", "call_id": call_id}))
                error = json.loads(await second.recv())
                self.assertEqual(error["type"], "error")
                self.assertEqual(error["code"], "permission")
                self.assertNotIn(("hold", "agent@example.test", call_id), self.runtime.actions)
            finally:
                await first.close(); await second.close()
        asyncio.run(run())

    def test_same_tab_reconnect_receives_active_snapshot_and_restores_handset(self):
        async def run():
            first, _ = await self._connect(tab_id="stable-tab")
            await first.send(json.dumps({"type": "call.dial", "number": "3000"}))
            created = json.loads(await first.recv())
            call_id = created["call"]["call_id"]
            self.runtime.snapshots = {call_id: {
                "user": "agent@example.test", "direction": "outgoing", "state": "connected",
                "from_number": "2001", "to_number": "3000", "connected_duration_seconds": 12.0,
            }}
            await first.close()
            await asyncio.sleep(0.05)
            second, ready = await self._connect(tab_id="stable-tab")
            try:
                self.assertIn(call_id, ready.get("restored_calls", []))
                snapshot = json.loads(await second.recv())
                self.assertEqual(snapshot["type"], "call.snapshot")
                self.assertEqual(snapshot["snapshot"]["call_id"], call_id)
                self.assertTrue(snapshot["snapshot"]["auto_restored"])
                self.assertEqual(snapshot["snapshot"]["handset"]["status"], "owner")
                self.assertEqual(snapshot["snapshot"]["connected_duration_seconds"], 12.0)
            finally:
                await second.close()
        asyncio.run(run())

    def test_ticket_replay_is_rejected(self):
        async def run():
            ticket = create_ticket(site=self.site, user="agent@example.test", bench_path=self.bench_path)
            first = await websockets.unix_connect(str(self.socket_path), uri="ws://localhost/telephony-voice", compression=None)
            await first.send(json.dumps({"type": "auth", "ticket": ticket, "protocol_version": 2}))
            self.assertEqual(json.loads(await first.recv())["type"], "session.ready")
            second = await websockets.unix_connect(str(self.socket_path), uri="ws://localhost/telephony-voice", compression=None)
            await second.send(json.dumps({"type": "auth", "ticket": ticket, "protocol_version": 2}))
            error = json.loads(await second.recv())
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["code"], "authentication")
            await first.close(); await second.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
