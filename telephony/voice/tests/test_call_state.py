from __future__ import annotations

import unittest

from telephony.voice.call_state import (
    CallStateTransitionError,
    TelephonyCallDirection,
    TelephonyCallState,
    TelephonyCallStateMachine,
)
from telephony.voice.sip import SipCallDirection, SipCallState


class TelephonyCallStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = TelephonyCallStateMachine()
        self.events = []
        self.machine.on_transition(self.events.append)

    def test_outgoing_sip_lifecycle_is_normalized(self):
        call_id = "outgoing"
        self.machine.observe_sip_state(call_id, SipCallState.DIALING)
        self.machine.observe_sip_state(call_id, SipCallState.RINGING)
        self.machine.observe_sip_state(call_id, SipCallState.CONNECTED)
        self.machine.transition(call_id, TelephonyCallState.DISCONNECTING)
        final = self.machine.observe_sip_state(call_id, SipCallState.ENDED)

        self.assertEqual(final.state, TelephonyCallState.ENDED)
        self.assertEqual(final.direction, TelephonyCallDirection.OUTGOING)

    def test_outgoing_ringing_as_first_sip_state_synthesizes_dialing(self):
        call_id = "fast-outgoing"
        final = self.machine.observe_sip_state(
            call_id,
            SipCallState.RINGING,
            direction=SipCallDirection.OUTGOING,
        )
        states = [event.state for event in self.events if event.call_id == call_id]
        self.assertEqual(
            states,
            [TelephonyCallState.NEW, TelephonyCallState.DIALING, TelephonyCallState.RINGING],
        )
        self.assertEqual(final.direction, TelephonyCallDirection.OUTGOING)

    def test_incoming_answer_exposes_connecting(self):
        call_id = "incoming"
        self.machine.create(call_id, TelephonyCallDirection.INCOMING)
        self.machine.observe_sip_state(
            call_id,
            SipCallState.RINGING,
            direction=SipCallDirection.INCOMING,
        )
        connecting = self.machine.transition(call_id, TelephonyCallState.CONNECTING)
        connected = self.machine.observe_sip_state(call_id, SipCallState.CONNECTED)

        self.assertEqual(connecting.state, TelephonyCallState.CONNECTING)
        self.assertEqual(connected.state, TelephonyCallState.CONNECTED)
        self.assertEqual(connected.direction, TelephonyCallDirection.INCOMING)

    def test_duplicate_and_late_sip_events_are_harmless(self):
        call_id = "late"
        ringing = self.machine.observe_sip_state(call_id, SipCallState.RINGING)
        duplicate = self.machine.observe_sip_state(call_id, SipCallState.RINGING)
        self.machine.transition(call_id, TelephonyCallState.CONNECTING)
        late = self.machine.observe_sip_state(call_id, SipCallState.RINGING)

        self.assertEqual(duplicate.sequence, ringing.sequence)
        self.assertEqual(late.state, TelephonyCallState.CONNECTING)
        self.assertEqual(late.sequence, ringing.sequence + 1)

    def test_held_call_ignores_duplicate_connected_sip_state_until_application_resumes(self):
        machine = TelephonyCallStateMachine()
        machine.observe_sip_state("call-1", SipCallState.DIALING, direction=SipCallDirection.OUTGOING)
        machine.observe_sip_state("call-1", SipCallState.CONNECTED)
        machine.transition("call-1", TelephonyCallState.HELD)

        duplicate = machine.observe_sip_state("call-1", SipCallState.CONNECTED)
        self.assertEqual(duplicate.state, TelephonyCallState.HELD)

        resumed = machine.transition("call-1", TelephonyCallState.CONNECTED)
        self.assertEqual(resumed.state, TelephonyCallState.CONNECTED)

    def test_disconnecting_ignores_late_connected_event(self):
        call_id = "disconnect"
        self.machine.observe_sip_state(call_id, SipCallState.DIALING)
        self.machine.observe_sip_state(call_id, SipCallState.CONNECTED)
        self.machine.transition(call_id, TelephonyCallState.DISCONNECTING)
        stale = self.machine.observe_sip_state(call_id, SipCallState.CONNECTED)
        ended = self.machine.observe_sip_state(call_id, SipCallState.ENDED)

        self.assertEqual(stale.state, TelephonyCallState.DISCONNECTING)
        self.assertEqual(ended.state, TelephonyCallState.ENDED)

    def test_invalid_application_transition_is_rejected(self):
        call_id = "invalid"
        self.machine.create(call_id, TelephonyCallDirection.OUTGOING)
        with self.assertRaises(CallStateTransitionError):
            self.machine.transition(call_id, TelephonyCallState.CONNECTED)

    def test_failed_is_terminal_and_keeps_reason(self):
        call_id = "failed"
        self.machine.observe_sip_state(call_id, SipCallState.DIALING)
        failed = self.machine.transition(
            call_id,
            TelephonyCallState.FAILED,
            failure_reason="network",
        )
        late = self.machine.observe_sip_state(call_id, SipCallState.ENDED)

        self.assertEqual(failed.state, TelephonyCallState.FAILED)
        self.assertEqual(failed.failure_reason, "network")
        self.assertEqual(late.state, TelephonyCallState.FAILED)

    def test_transition_callback_has_monotonic_sequence(self):
        call_id = "sequence"
        self.machine.observe_sip_state(call_id, SipCallState.DIALING)
        self.machine.observe_sip_state(call_id, SipCallState.RINGING)
        self.machine.observe_sip_state(call_id, SipCallState.CONNECTED)

        call_events = [event for event in self.events if event.call_id == call_id]
        self.assertEqual(
            [event.state for event in call_events],
            [
                TelephonyCallState.NEW,
                TelephonyCallState.DIALING,
                TelephonyCallState.RINGING,
                TelephonyCallState.CONNECTED,
            ],
        )
        self.assertEqual([event.sequence for event in call_events], [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
