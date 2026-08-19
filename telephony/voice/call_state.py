from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from time import monotonic
from typing import Callable

from telephony.voice.sip.models import SipCallDirection, SipCallState


class TelephonyCallState(str, Enum):
    NEW = "new"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    HELD = "held"
    DISCONNECTING = "disconnecting"
    ENDED = "ended"
    FAILED = "failed"


class TelephonyCallDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class CallStateError(RuntimeError):
    pass


class CallStateTransitionError(CallStateError):
    pass


@dataclass(frozen=True, slots=True)
class TelephonyCallSnapshot:
    call_id: str
    direction: TelephonyCallDirection
    state: TelephonyCallState
    previous_state: TelephonyCallState | None
    sequence: int
    changed_at: float
    failure_reason: str | None = None


CallStateCallback = Callable[[TelephonyCallSnapshot], None]


_ALLOWED_TRANSITIONS: dict[TelephonyCallState, frozenset[TelephonyCallState]] = {
    TelephonyCallState.NEW: frozenset({
        TelephonyCallState.DIALING,
        TelephonyCallState.RINGING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.DIALING: frozenset({
        TelephonyCallState.RINGING,
        TelephonyCallState.CONNECTING,
        TelephonyCallState.CONNECTED,
        TelephonyCallState.DISCONNECTING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.RINGING: frozenset({
        TelephonyCallState.CONNECTING,
        TelephonyCallState.CONNECTED,
        TelephonyCallState.DISCONNECTING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.CONNECTING: frozenset({
        TelephonyCallState.CONNECTED,
        TelephonyCallState.DISCONNECTING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.CONNECTED: frozenset({
        TelephonyCallState.HELD,
        TelephonyCallState.DISCONNECTING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.HELD: frozenset({
        TelephonyCallState.CONNECTED,
        TelephonyCallState.DISCONNECTING,
        TelephonyCallState.FAILED,
        TelephonyCallState.ENDED,
    }),
    TelephonyCallState.DISCONNECTING: frozenset({
        TelephonyCallState.ENDED,
        TelephonyCallState.FAILED,
    }),
    TelephonyCallState.ENDED: frozenset(),
    TelephonyCallState.FAILED: frozenset(),
}

_SIP_STATE_MAP = {
    SipCallState.DIALING: TelephonyCallState.DIALING,
    SipCallState.RINGING: TelephonyCallState.RINGING,
    SipCallState.CONNECTED: TelephonyCallState.CONNECTED,
    SipCallState.ENDED: TelephonyCallState.ENDED,
}

_SIP_DIRECTION_MAP = {
    SipCallDirection.INCOMING: TelephonyCallDirection.INCOMING,
    SipCallDirection.OUTGOING: TelephonyCallDirection.OUTGOING,
}


class TelephonyCallStateMachine:
    def __init__(self) -> None:
        self._lock = RLock()
        self._calls: dict[str, TelephonyCallSnapshot] = {}
        self._callback: CallStateCallback | None = None

    def on_transition(self, callback: CallStateCallback | None) -> None:
        with self._lock:
            self._callback = callback

    def create(
        self,
        call_id: str,
        direction: TelephonyCallDirection,
    ) -> TelephonyCallSnapshot:
        with self._lock:
            existing = self._calls.get(call_id)
            if existing is not None:
                if existing.direction != direction:
                    existing = replace(existing, direction=direction)
                    self._calls[call_id] = existing
                return existing
            snapshot = TelephonyCallSnapshot(
                call_id=call_id,
                direction=direction,
                state=TelephonyCallState.NEW,
                previous_state=None,
                sequence=0,
                changed_at=monotonic(),
            )
            self._calls[call_id] = snapshot
        self._notify(snapshot)
        return snapshot

    def get(self, call_id: str) -> TelephonyCallSnapshot:
        with self._lock:
            snapshot = self._calls.get(call_id)
        if snapshot is None:
            raise CallStateError(f"Unknown Telephony call: {call_id}")
        return snapshot

    def transition(
        self,
        call_id: str,
        state: TelephonyCallState,
        *,
        failure_reason: str | None = None,
        ignore_invalid: bool = False,
    ) -> TelephonyCallSnapshot:
        with self._lock:
            current = self._calls.get(call_id)
            if current is None:
                raise CallStateError(f"Unknown Telephony call: {call_id}")
            if current.state == state:
                return current
            if state not in _ALLOWED_TRANSITIONS[current.state]:
                if ignore_invalid:
                    return current
                raise CallStateTransitionError(
                    f"Invalid Telephony call transition: {current.state.value} -> {state.value}"
                )
            snapshot = replace(
                current,
                state=state,
                previous_state=current.state,
                sequence=current.sequence + 1,
                changed_at=monotonic(),
                failure_reason=failure_reason if state == TelephonyCallState.FAILED else current.failure_reason,
            )
            self._calls[call_id] = snapshot
        self._notify(snapshot)
        return snapshot

    def observe_sip_state(
        self,
        call_id: str,
        sip_state: SipCallState,
        *,
        direction: SipCallDirection | TelephonyCallDirection | None = None,
    ) -> TelephonyCallSnapshot:
        telephony_direction = self._direction(direction, sip_state)
        with self._lock:
            current = self._calls.get(call_id)
        if current is None:
            self.create(call_id, telephony_direction)
            if telephony_direction == TelephonyCallDirection.OUTGOING and sip_state != SipCallState.DIALING:
                self.transition(call_id, TelephonyCallState.DIALING)
        elif direction is not None and current.direction != telephony_direction:
            with self._lock:
                self._calls[call_id] = replace(current, direction=telephony_direction)

        target = _SIP_STATE_MAP[sip_state]
        current = self.get(call_id)
        if current.state in {TelephonyCallState.ENDED, TelephonyCallState.FAILED}:
            return current
        if current.state == TelephonyCallState.DISCONNECTING and target != TelephonyCallState.ENDED:
            return current
        if current.state == TelephonyCallState.HELD and target == TelephonyCallState.CONNECTED:
            return current
        if current.state == TelephonyCallState.CONNECTING and target == TelephonyCallState.RINGING:
            return current
        return self.transition(call_id, target, ignore_invalid=True)

    def is_active(self, call_id: str) -> bool:
        return self.get(call_id).state not in {
            TelephonyCallState.ENDED,
            TelephonyCallState.FAILED,
        }

    @staticmethod
    def _direction(
        direction: SipCallDirection | TelephonyCallDirection | None,
        sip_state: SipCallState,
    ) -> TelephonyCallDirection:
        if isinstance(direction, TelephonyCallDirection):
            return direction
        if isinstance(direction, SipCallDirection):
            return _SIP_DIRECTION_MAP[direction]
        if sip_state == SipCallState.DIALING:
            return TelephonyCallDirection.OUTGOING
        return TelephonyCallDirection.INCOMING

    def _notify(self, snapshot: TelephonyCallSnapshot) -> None:
        with self._lock:
            callback = self._callback
        if callback is not None:
            callback(snapshot)


__all__ = [
    "CallStateError",
    "CallStateTransitionError",
    "TelephonyCallDirection",
    "TelephonyCallSnapshot",
    "TelephonyCallState",
    "TelephonyCallStateMachine",
]
