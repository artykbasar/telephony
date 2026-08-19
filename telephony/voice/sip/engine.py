from __future__ import annotations

from abc import ABC, abstractmethod

from telephony.voice.sip.models import (
    AudioCallback,
    CallStateCallback,
    IncomingCallCallback,
    SipAccountConfig,
    SipCallState,
    SipRegistrationState,
    SipTransferResult,
    TelephonyAudioFormat,
)


class SipEngineError(RuntimeError):
    pass


class SipEngineStateError(SipEngineError):
    pass


class SipCallNotFoundError(SipEngineError):
    pass


class SipEngineShutdownTimeout(SipEngineError):
    pass


class SipEngine(ABC):
    """Engine-independent SIP/RTP contract used by Telephony.

    Audio is pull/push based through ``read_audio`` and ``write_audio``. The
    optional ``on_audio`` callback observes frames returned by ``read_audio``;
    it does not create a competing background audio consumer.
    """

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_account(self, config: SipAccountConfig) -> SipRegistrationState:
        raise NotImplementedError

    @abstractmethod
    def unregister_account(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def registration_state(self) -> SipRegistrationState:
        raise NotImplementedError

    @property
    @abstractmethod
    def audio_format(self) -> TelephonyAudioFormat:
        raise NotImplementedError

    @abstractmethod
    def make_call(self, number: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def answer_call(self, call_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def reject_call(self, call_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def hangup_call(self, call_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_dtmf(self, call_id: str, digits: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def read_audio(
        self,
        call_id: str,
        length: int | None = None,
        *,
        blocking: bool = True,
    ) -> bytes:
        raise NotImplementedError

    def audio_available(self, call_id: str) -> int | None:
        """Return real received audio bytes currently buffered, if available."""
        return None

    def audio_output_available(self, call_id: str) -> int | None:
        """Return queued outbound audio bytes waiting for the engine's media clock."""
        return None

    def hold_call(self, call_id: str) -> None:
        raise SipEngineStateError("SIP hold is not supported by this engine.")

    def resume_call(self, call_id: str) -> None:
        raise SipEngineStateError("SIP resume is not supported by this engine.")

    def transfer_call(self, call_id: str, target: str) -> SipTransferResult:
        raise SipEngineStateError("SIP transfer is not supported by this engine.")

    def attended_transfer(self, call_id: str, consult_call_id: str) -> SipTransferResult:
        raise SipEngineStateError("Attended SIP transfer is not supported by this engine.")

    def capabilities(self) -> dict[str, object]:
        """Describe optional call/media features exposed by this SIP engine."""
        return {
            "dtmf": True,
            "hold": False,
            "transfer": False,
            "attended_transfer": False,
            "media_status": False,
            "codecs": [],
        }

    def call_media_status(self, call_id: str) -> dict[str, object]:
        """Return safe per-call media diagnostics when supported by the engine."""
        return {}

    def signaling_status(self) -> dict[str, object]:
        """Return safe live SIP transport diagnostics when supported by the engine."""
        return {}

    @abstractmethod
    def write_audio(self, call_id: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_call_state(self, call_id: str) -> SipCallState:
        raise NotImplementedError

    @abstractmethod
    def on_incoming_call(self, callback: IncomingCallCallback | None) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_call_state(self, callback: CallStateCallback | None) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_audio(self, callback: AudioCallback | None) -> None:
        raise NotImplementedError
