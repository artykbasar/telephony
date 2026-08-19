from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class SipRegistrationState(str, Enum):
    INACTIVE = "inactive"
    REGISTERING = "registering"
    REGISTERED = "registered"
    DEREGISTERING = "deregistering"
    FAILED = "failed"


class SipCallState(str, Enum):
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTED = "connected"
    ENDED = "ended"


class SipCallOutcome(str, Enum):
    COMPLETED = "Completed"
    FAILED = "Failed"
    BUSY = "Busy"
    NO_ANSWER = "No Answer"
    CANCELED = "Canceled"


class SipCallDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


@dataclass(frozen=True, slots=True)
class TelephonyAudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16
    frame_ms: int = 20

    @property
    def frame_size(self) -> int:
        samples = round(self.sample_rate * self.frame_ms / 1000)
        return samples * self.channels * (self.bit_depth // 8)


@dataclass(frozen=True, slots=True)
class SipAccountConfig:
    server: str
    username: str
    password: str = field(repr=False)
    port: int = 5060
    auth_username: str | None = None
    proxy: str | None = None
    proxy_port: int | None = None
    transport: str = "UDP"
    local_ip: str = "0.0.0.0"
    advertised_ip: str | None = None
    tls_context: Any | None = field(default=None, repr=False, compare=False)
    tls_server_name: str | None = None
    audio_format: TelephonyAudioFormat = field(default_factory=TelephonyAudioFormat)


@dataclass(frozen=True, slots=True)
class SipIncomingCall:
    call_id: str
    caller_id: str | None
    called_number: str | None
    caller_name: str | None = None
    ivr_route: str | None = None


@dataclass(frozen=True, slots=True)
class SipTransferResult:
    mode: str
    target: str
    accepted: bool
    completed: bool
    status_code: int | None = None
    phrase: str | None = None
    replaces_call_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target": self.target,
            "accepted": self.accepted,
            "completed": self.completed,
            "status_code": self.status_code,
            "phrase": self.phrase,
            "replaces_call_id": self.replaces_call_id,
        }


IncomingCallCallback = Callable[[SipIncomingCall], None]
CallStateCallback = Callable[[str, SipCallState], None]
AudioCallback = Callable[[str, bytes], None]
