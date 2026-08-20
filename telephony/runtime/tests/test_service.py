from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telephony.runtime.accounts import TelephonyRuntimeAccount
from telephony.runtime.service import TelephonyRuntimeAccountNotFound, TelephonyRuntimeError, TelephonySipRuntime
from telephony.voice.sip import SipAccountConfig, SipIncomingCall, SipRegistrationState, SipTransferResult


class _Engine:
    def __init__(self):
        self.started = False
        self.config = None
        self.incoming = None
        self.state_callback = None
        self.calls = []
        self.actions = []
        self._state = SipRegistrationState.INACTIVE
        self.register_count = 0

    def on_incoming_call(self, callback): self.incoming = callback
    def on_call_state(self, callback): self.state_callback = callback
    def start(self): self.started = True
    def register_account(self, config):
        self.config = config
        self.register_count += 1
        self._state = SipRegistrationState.REGISTERED
        return self._state
    def stop(self): self.started = False; self._state = SipRegistrationState.INACTIVE
    @property
    def registration_state(self): return self._state
    def make_call(self, number):
        call_id = f"{self.config.username}-{number}"
        self.calls.append(number)
        return call_id
    def answer_call(self, call_id): self.actions.append(("answer", call_id))
    def reject_call(self, call_id): self.actions.append(("reject", call_id))
    def hangup_call(self, call_id): self.actions.append(("hangup", call_id))
    def hold_call(self, call_id): self.actions.append(("hold", call_id))
    def resume_call(self, call_id): self.actions.append(("resume", call_id))
    def send_dtmf(self, call_id, digit): self.actions.append(("dtmf", call_id, digit)); return True
    def transfer_call(self, call_id, target):
        self.actions.append(("transfer", call_id, target)); return SipTransferResult("blind", target, True, True)
    def attended_transfer(self, call_id, consult_call_id):
        self.actions.append(("attended", call_id, consult_call_id)); return SipTransferResult("attended", consult_call_id, True, True)
    def call_media_status(self, call_id):
        return {"codec": "OPUS", "rtp_streams": 1, "local_rtp_ports": [40000]}



class _FakeBridge:
    def __init__(self, engine, call_id, send_to_browser, **kwargs):
        self.engine = engine
        self.call_id = call_id
        self.send_to_browser = send_to_browser
        self.running = False
        self.held = False
        self.audio_format = SimpleNamespace(sample_rate=16000, channels=1, bit_depth=16, frame_ms=20)
        self.frame_size = 640

    def start(self): self.running = True
    def stop(self): self.running = False
    def set_held(self, held): self.held = bool(held)
    def status(self):
        return {
            "browser_frames_received": 0, "browser_frames_dropped": 0,
            "sip_frames_written": 0, "sip_frames_read": 0, "sip_frames_dropped": 0,
            "sip_null_ticks": 0, "browser_frames_sent": 0,
            "flow_xoff_events": 0, "flow_xon_events": 0,
        }


class _CallLog:
    def __init__(self): self.events = []
    def outgoing_started(self, account, call_id, number): self.events.append(("outgoing", account.user, call_id, number))
    def incoming_started(self, account, call): self.events.append(("incoming", account.user, call.call_id))
    def state_changed(self, account, call_id, state, outcome=None):
        self.events.append(("state", account.user, call_id, state.value, getattr(outcome, "value", None)))


def _account(user, username, *, server="pbx.example.test"):
    return TelephonyRuntimeAccount(
        agent=user, user=user, extension=username, display_name=user,
        config=SipAccountConfig(server=server, username=username, password="secret"),
    )


class TelephonySipRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.created = []
        def factory():
            engine = _Engine(); self.created.append(engine); return engine
        self.incoming = []
        self.call_log = _CallLog()
        self.runtime = TelephonySipRuntime(
            (_account("alice@example.test", "2001"), _account("bob@example.test", "2002")),
            engine_factory=factory,
            incoming_callback=lambda account, call: self.incoming.append((account.user, call.call_id)),
            call_log_writer=self.call_log,
            registration_monitor_interval=0.01, registration_recovery_backoff=(0.01,),
        )

    def tearDown(self):
        if self.runtime._started:
            self.runtime.stop()

    def test_two_agents_register_independently(self):
        self.runtime.start()
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.created[0].config.username, "2001")
        self.assertEqual(self.created[1].config.username, "2002")
        self.assertEqual(self.runtime.registration_snapshot(), {
            "alice@example.test": "registered", "bob@example.test": "registered",
        })
        self.runtime.stop()

    def test_outgoing_user_selects_only_that_users_engine(self):
        self.runtime.start()
        call_id = self.runtime.dial(user="bob@example.test", number="441234567890")
        self.assertEqual(call_id, "2002-441234567890")
        self.assertEqual(self.created[0].calls, [])
        self.assertEqual(self.created[1].calls, ["441234567890"])
        self.assertEqual(self.runtime.account_for_call(call_id).user, "bob@example.test")
        self.assertIn(("outgoing", "bob@example.test", call_id, "441234567890"), self.call_log.events)

    def test_incoming_account_maps_directly_to_frappe_user(self):
        self.runtime.start()
        self.created[0].incoming(SipIncomingCall(call_id="incoming-a", caller_id="100", called_number="2001"))
        self.created[1].incoming(SipIncomingCall(call_id="incoming-b", caller_id="101", called_number="2002"))
        self.assertEqual(self.incoming, [
            ("alice@example.test", "incoming-a"), ("bob@example.test", "incoming-b"),
        ])
        self.assertEqual(self.runtime.account_for_call("incoming-a").user, "alice@example.test")
        self.assertEqual(self.runtime.account_for_call("incoming-b").user, "bob@example.test")
        self.assertIn(("incoming", "alice@example.test", "incoming-a"), self.call_log.events)
        self.created[0].state_callback("incoming-a", __import__("telephony.voice.sip", fromlist=["SipCallState"]).SipCallState.CONNECTED)
        self.assertIn(("state", "alice@example.test", "incoming-a", "connected", None), self.call_log.events)

    def test_unknown_user_cannot_borrow_another_agents_registration(self):
        self.runtime.start()
        with self.assertRaises(TelephonyRuntimeAccountNotFound):
            self.runtime.dial(user="mallory@example.test", number="123")

    def test_active_snapshot_tracks_server_state_and_connected_duration(self):
        self.runtime.start()
        call_id = self.runtime.dial(user="alice@example.test", number="3000")
        self.created[0].state_callback(call_id, __import__("telephony.voice.sip", fromlist=["SipCallState"]).SipCallState.CONNECTED)
        snapshots = self.runtime.active_call_snapshots(user="alice@example.test")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["state"], "connected")
        self.assertEqual(snapshots[0]["direction"], "outgoing")
        self.assertEqual(snapshots[0]["to_number"], "3000")
        self.assertIsNotNone(snapshots[0]["connected_at"])
        self.assertGreaterEqual(snapshots[0]["connected_duration_seconds"], 0)
        self.runtime.hold(user="alice@example.test", call_id=call_id)
        self.assertEqual(self.runtime.active_call_snapshots(user="alice@example.test")[0]["state"], "held")
        self.runtime.resume(user="alice@example.test", call_id=call_id)
        self.assertEqual(self.runtime.active_call_snapshots(user="alice@example.test")[0]["state"], "connected")

    def test_call_controls_are_confined_to_runtime_owner(self):
        self.runtime.start()
        call_id = self.runtime.dial(user="alice@example.test", number="3000")
        self.runtime.hold(user="alice@example.test", call_id=call_id)
        self.runtime.resume(user="alice@example.test", call_id=call_id)
        self.runtime.dtmf(user="alice@example.test", call_id=call_id, digit="5")
        self.runtime.transfer(user="alice@example.test", call_id=call_id, target="3001")
        self.assertIn(("hold", call_id), self.created[0].actions)
        self.assertIn(("resume", call_id), self.created[0].actions)
        self.assertIn(("dtmf", call_id, "5"), self.created[0].actions)
        self.assertIn(("transfer", call_id, "3001"), self.created[0].actions)
        with self.assertRaises(TelephonyRuntimeAccountNotFound):
            self.runtime.hangup(user="bob@example.test", call_id=call_id)

    def test_answer_transitions_to_connecting_before_sip_answer(self):
        self.runtime.start()
        self.created[0].incoming(SipIncomingCall(call_id="incoming-answer", caller_id="100", called_number="2001"))
        self.assertEqual(self.runtime.calls.get("incoming-answer").state.value, "ringing")
        self.runtime.answer(user="alice@example.test", call_id="incoming-answer")
        self.assertEqual(self.runtime.calls.get("incoming-answer").state.value, "connecting")
        self.assertIn(("answer", "incoming-answer"), self.created[0].actions)

    def test_media_is_single_bridge_per_call_and_hold_state_is_runtime_owned(self):
        self.runtime.start()
        call_id = self.runtime.dial(user="alice@example.test", number="3000")
        sip = __import__("telephony.voice.sip", fromlist=["SipCallState"])
        self.created[0].state_callback(call_id, sip.SipCallState.CONNECTED)
        self.runtime.voice_client_connected("alice@example.test", "media-session", tab_id="media-tab")
        self.assertTrue(self.runtime.voice_claim_handset(
            call_id, user="alice@example.test", session_id="media-session"
        )["owner"])
        with patch("telephony.runtime.service.PcmMediaBridge", _FakeBridge):
            first = self.runtime.start_media(user="alice@example.test", session_id="media-session", call_id=call_id, send_to_browser=lambda frame: None)
            self.assertTrue(first.running)
            with self.assertRaisesRegex(TelephonyRuntimeError, "already attached"):
                self.runtime.start_media(user="alice@example.test", session_id="media-session", call_id=call_id, send_to_browser=lambda frame: None)
            self.runtime.hold(user="alice@example.test", call_id=call_id)
            self.assertTrue(first.held)
            self.runtime.resume(user="alice@example.test", call_id=call_id)
            self.assertFalse(first.held)
            self.runtime.stop_media(first)
            self.assertFalse(first.running)
            second = self.runtime.start_media(user="alice@example.test", session_id="media-session", call_id=call_id, send_to_browser=lambda frame: None)
            self.assertTrue(second.running)
            self.runtime.stop_media(second)

    def test_runtime_owns_handset_disconnect_grace_and_same_tab_restore(self):
        runtime = TelephonySipRuntime(
            (_account("alice@example.test", "2001"),), engine_factory=lambda: _Engine(),
            call_log_writer=_CallLog(), browser_disconnect_grace_seconds=0.2,
            registration_monitor_interval=1.0,
        )
        runtime.start()
        try:
            call_id = runtime.dial(user="alice@example.test", number="3000")
            runtime.voice_client_connected("alice@example.test", "session-a", tab_id="stable-tab")
            claimed = runtime.voice_claim_handset(call_id, user="alice@example.test", session_id="session-a")
            self.assertEqual(claimed["status"], "owner")
            orphaned = runtime.voice_client_disconnected("alice@example.test", "session-a")
            self.assertEqual(orphaned, [call_id])
            self.assertEqual(runtime.voice_handset_state(
                call_id, user="alice@example.test", session_id="session-b"
            )["status"], "orphaned")
            runtime.voice_client_connected("alice@example.test", "session-b", tab_id="stable-tab")
            restored = runtime.voice_restore_handsets(
                user="alice@example.test", session_id="session-b", tab_id="stable-tab"
            )
            self.assertEqual(restored, [call_id])
            self.assertTrue(runtime.voice_handset_is_owner(
                call_id, user="alice@example.test", session_id="session-b"
            ))
            time.sleep(0.25)
            self.assertNotIn(("hangup", call_id), runtime.engines["alice@example.test"].actions)
        finally:
            runtime.stop()

    def test_runtime_expires_orphaned_handset_after_grace(self):
        engine = _Engine()
        runtime = TelephonySipRuntime(
            (_account("alice@example.test", "2001"),), engine_factory=lambda: engine,
            call_log_writer=_CallLog(), browser_disconnect_grace_seconds=0.03,
            registration_monitor_interval=1.0,
        )
        runtime.start()
        try:
            call_id = runtime.dial(user="alice@example.test", number="3000")
            runtime.voice_client_connected("alice@example.test", "session-a", tab_id="tab-a")
            runtime.voice_claim_handset(call_id, user="alice@example.test", session_id="session-a")
            runtime.voice_client_disconnected("alice@example.test", "session-a")
            deadline = time.time() + 0.5
            while time.time() < deadline and ("hangup", call_id) not in engine.actions:
                time.sleep(0.01)
            self.assertIn(("hangup", call_id), engine.actions)
            self.assertEqual(runtime.calls.get(call_id).state.value, "disconnecting")
        finally:
            runtime.stop()

    def test_registration_loss_restarts_only_failed_agents_engine(self):
        self.runtime.start()
        first, second = self.created
        first._state = SipRegistrationState.FAILED
        deadline = time.time() + 1.0
        while time.time() < deadline and first.register_count < 2:
            time.sleep(0.01)
        self.assertGreaterEqual(first.register_count, 2)
        self.assertEqual(first.registration_state, SipRegistrationState.REGISTERED)
        self.assertEqual(second.register_count, 1)

    def test_reconcile_changed_account_reloads_only_that_agent_and_drops_its_call(self):
        self.runtime.start()
        first, second = self.created
        call_id = self.runtime.dial(user="alice@example.test", number="3000")
        changed_alice = _account("alice@example.test", "2001", server="asterisk")
        bob = _account("bob@example.test", "2002")

        changes = self.runtime.reconcile_accounts((changed_alice, bob))

        self.assertEqual(changes, {
            "added": (), "changed": ("alice@example.test",), "removed": (),
        })
        self.assertEqual(len(self.created), 3)
        replacement = self.created[2]
        self.assertIs(self.runtime.engines["alice@example.test"], replacement)
        self.assertIs(self.runtime.engines["bob@example.test"], second)
        self.assertFalse(first.started)
        self.assertTrue(second.started)
        self.assertEqual(second.register_count, 1)
        self.assertEqual(replacement.config.server, "asterisk")
        self.assertEqual(replacement.register_count, 1)
        self.assertIn(("hangup", call_id), first.actions)
        self.assertEqual(self.runtime.calls.get(call_id).state.value, "disconnecting")
        self.assertEqual(self.runtime.registration_snapshot(), {
            "alice@example.test": "registered", "bob@example.test": "registered",
        })

    def test_reconcile_unchanged_accounts_does_not_reload_engines(self):
        self.runtime.start()
        first, second = self.created

        changes = self.runtime.reconcile_accounts((
            _account("alice@example.test", "2001"),
            _account("bob@example.test", "2002"),
        ))

        self.assertEqual(changes, {"added": (), "changed": (), "removed": ()})
        self.assertEqual(self.created, [first, second])
        self.assertEqual(first.register_count, 1)
        self.assertEqual(second.register_count, 1)

    def test_reconcile_adds_and_removes_only_affected_agents(self):
        self.runtime.start()
        alice_engine, bob_engine = self.created
        bob_call = self.runtime.dial(user="bob@example.test", number="3000")
        alice = _account("alice@example.test", "2001")
        charlie = _account("charlie@example.test", "2003")

        changes = self.runtime.reconcile_accounts((alice, charlie))

        self.assertEqual(changes, {
            "added": ("charlie@example.test",),
            "changed": (),
            "removed": ("bob@example.test",),
        })
        self.assertIs(self.runtime.engines["alice@example.test"], alice_engine)
        self.assertFalse(bob_engine.started)
        self.assertIn(("hangup", bob_call), bob_engine.actions)
        self.assertNotIn("bob@example.test", self.runtime.engines)
        self.assertEqual(self.runtime.engines["charlie@example.test"].config.username, "2003")
        with self.assertRaises(TelephonyRuntimeAccountNotFound):
            self.runtime.account_for_user("bob@example.test")
        with self.assertRaises(TelephonyRuntimeAccountNotFound):
            self.runtime.account_for_call(bob_call)
        self.assertEqual(self.runtime.active_call_snapshots(user="alice@example.test"), [])


if __name__ == "__main__":
    unittest.main()
