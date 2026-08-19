from telephony.voice.sip.engine import (
    SipCallNotFoundError,
    SipEngine,
    SipEngineError,
    SipEngineShutdownTimeout,
    SipEngineStateError,
)
from telephony.voice.sip.models import (
    SipAccountConfig,
    SipCallDirection,
    SipCallState,
    SipIncomingCall,
    SipRegistrationState,
    SipTransferResult,
    TelephonyAudioFormat,
)

__all__ = [
    "RfcVoipEngine", "SipAccountConfig", "SipCallDirection", "SipCallNotFoundError",
    "SipCallState", "SipEngine", "SipEngineError", "SipEngineShutdownTimeout",
    "SipEngineStateError", "SipIncomingCall", "SipRegistrationState", "SipTransferResult",
    "TelephonyAudioFormat",
]


def __getattr__(name):
    if name == "RfcVoipEngine":
        from telephony.voice.sip.rfcvoip_engine import RfcVoipEngine

        return RfcVoipEngine
    raise AttributeError(name)
