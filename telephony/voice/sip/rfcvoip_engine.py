from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

from rfcvoip import codecs as rfcvoip_codecs
from rfcvoip.RTP import PayloadType
from rfcvoip.codecs.opus import OpusCodec
from rfcvoip.VoIP import CallState, PhoneStatus, VoIPCall, VoIPPhone

from telephony.voice.sip.engine import (
    SipCallNotFoundError,
    SipEngine,
    SipEngineShutdownTimeout,
    SipEngineStateError,
)
from telephony.voice.sip.models import (
    AudioCallback,
    CallStateCallback,
    IncomingCallCallback,
    SipAccountConfig,
    SipCallDirection,
    SipCallOutcome,
    SipCallState,
    SipIncomingCall,
    SipRegistrationState,
    SipTransferResult,
    TelephonyAudioFormat,
)
from telephony.voice.sip.rfcvoip_dialog import RfcVoipDialogController
from telephony.voice.sip.rfcvoip_dynamic_ports import (
    install_rfcvoip_dynamic_port_compatibility,
    release_all_reserved_rtp_ports,
    release_reserved_rtp_ports,
    reserve_rtp_ports,
)
from telephony.voice.sip.rfcvoip_opus import install_bundled_opus_compatibility
from telephony.voice.sip.rfcvoip_sdp import install_rfcvoip_sdp_advertised_ip_compatibility
from telephony.voice.sip.rfcvoip_timing import install_rfcvoip_timing_compatibility

logger = logging.getLogger(__name__)

install_bundled_opus_compatibility()
install_rfcvoip_timing_compatibility()
install_rfcvoip_sdp_advertised_ip_compatibility()
install_rfcvoip_dynamic_port_compatibility()


# RFCVoIP 2.10.2 decodes Opus with ``max_frame_size=576`` samples, but a
# standard 20 ms Opus frame at 48 kHz contains 960 samples per channel. libopus
# therefore returns OPUS_BUFFER_TOO_SMALL as soon as Opus is actually selected.
# Use libopus' maximum legal 120 ms frame capacity (5760 samples/channel). Keep
# this conditional so a future RFCVoIP release with the corrected value wins.
if int(getattr(OpusCodec, "max_frame_size", 0) or 0) < 960:
    OpusCodec.max_frame_size = 5760


_TELEPHONY_AUDIO_CODEC_ORDER = (
    PayloadType.OPUS,
    PayloadType.G722,
    PayloadType.PCMU,
    PayloadType.PCMA,
)


def _available_telephony_audio_codecs() -> list[PayloadType]:
    enabled = set(rfcvoip_codecs.enabled_payload_types(include_events=False))
    return [codec for codec in _TELEPHONY_AUDIO_CODEC_ORDER if codec in enabled]


class _RfcVoipPhone(VoIPPhone):
    """RFCVoIP phone restricted to Telephony's supported voice codec set."""

    # Keep the production speech codec set explicit while leaving RFCVoIP
    # responsible for actual availability and
    # codec conversion. RFCVoIP's built-in priority order is OPUS > G722 >
    # PCMU > PCMA, and telephone-event remains a non-audio DTMF payload.
    _allowed_codecs = set(_TELEPHONY_AUDIO_CODEC_ORDER) | {PayloadType.EVENT}

    def __init__(self, *args, **kwargs):
        # Telephony is a SIP client. Let the OS select its local signaling port;
        # the compatibility layer records the real socket port before REGISTER.
        kwargs["sipPort"] = 0
        kwargs.pop("rtpPortLow", None)
        kwargs.pop("rtpPortHigh", None)
        self._telephony_rtp_owner = id(self)
        self._telephony_final_invite_status: dict[str, int] = {}
        super().__init__(*args, **kwargs)
        # RFCVoIP 2.10.2 derives Call-ID from a process-local counter that
        # restarts at zero. That can collide with persisted Telephony calls after
        # a runtime restart. Keep the SIP library untouched and replace only
        # this phone instance's generator with an RFC-compliant random ID.
        self.sip.gen_call_id = self._gen_unique_call_id

    def _gen_unique_call_id(self) -> str:
        return f"{uuid.uuid4().hex}@{self.sip.myIP}:{self.sip.myPort}"

    def callback(self, request) -> None:
        cseq = getattr(request, "headers", {}).get("CSeq", {})
        method = cseq.get("method") if isinstance(cseq, dict) else None
        try:
            status_code = int(getattr(request, "status", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        call_id = str(getattr(request, "headers", {}).get("Call-ID", "") or "")
        if method == "INVITE" and status_code >= 200 and call_id:
            with self._call_state_lock:
                self._telephony_final_invite_status[call_id] = status_code
        super().callback(request)

    def final_invite_status(self, call_id: str) -> int | None:
        with self._call_state_lock:
            return self._telephony_final_invite_status.get(str(call_id))

    def request_ports(self, count: int, blocking=True) -> list[int]:
        del blocking
        ports = reserve_rtp_ports(self.myIP, count, owner=self._telephony_rtp_owner)
        with self.portsLock:
            self.assignedPorts.extend(ports)
        return ports

    def release_ports(self, call=None) -> None:
        with self.portsLock:
            before = set(self.assignedPorts)
        super().release_ports(call=call)
        with self.portsLock:
            released = before.difference(self.assignedPorts)
        release_reserved_rtp_ports(owner=self._telephony_rtp_owner, ports=released)

    def stop(self, failed=False) -> None:
        try:
            super().stop(failed=failed)
        finally:
            release_all_reserved_rtp_ports(owner=self._telephony_rtp_owner)

    @classmethod
    def available_audio_codecs(cls) -> list[PayloadType]:
        return _available_telephony_audio_codecs()

    def _prioritized_enabled_codecs(self):
        available = set(_available_telephony_audio_codecs())
        return [
            codec
            for codec in super()._prioritized_enabled_codecs()
            if codec in available or codec == PayloadType.EVENT
        ]

    def _create_Call(self, request, sess_id) -> None:
        # RFCVoIP 2.10.2 rejects the whole incoming INVITE when an otherwise
        # valid audio offer also contains an enabled video m-line. Telephony
        # Voice is deliberately audio-only, so disable unsupported video media
        # at the adapter boundary and let RFCVoIP negotiate the audio section.
        body = getattr(request, "body", {}) or {}
        media_sections = body.get("m", []) if isinstance(body, dict) else []
        for media in media_sections:
            if isinstance(media, dict) and media.get("type") == "video":
                media["port"] = 0
        super()._create_Call(request, sess_id)


@dataclass(slots=True)
class _TrackedCall:
    call: VoIPCall
    direction: SipCallDirection


_REGISTRATION_STATE_MAP = {
    PhoneStatus.INACTIVE: SipRegistrationState.INACTIVE,
    PhoneStatus.REGISTERING: SipRegistrationState.REGISTERING,
    PhoneStatus.REGISTERED: SipRegistrationState.REGISTERED,
    PhoneStatus.DEREGISTERING: SipRegistrationState.DEREGISTERING,
    PhoneStatus.FAILED: SipRegistrationState.FAILED,
}

_CALL_STATE_MAP = {
    CallState.DIALING: SipCallState.DIALING,
    CallState.RINGING: SipCallState.RINGING,
    CallState.ANSWERED: SipCallState.CONNECTED,
    CallState.ENDED: SipCallState.ENDED,
}


class RfcVoipEngine(SipEngine):
    """RFCVoIP-backed SIP engine with no Frappe/runtime dependency."""

    def __init__(
        self,
        *,
        state_poll_interval: float = 0.02,
        shutdown_timeout: float = 1.5,
    ):
        self._state_poll_interval = max(0.005, float(state_poll_interval))
        self._shutdown_timeout = max(0.1, float(shutdown_timeout))
        self._lock = threading.RLock()

        self._started = False
        self._phone: VoIPPhone | None = None
        self._account: SipAccountConfig | None = None

        self._calls: dict[str, _TrackedCall] = {}
        self._final_states: dict[str, SipCallState] = {}
        self._last_states: dict[str, SipCallState] = {}
        self._terminal_outcomes: dict[str, SipCallOutcome] = {}

        self._incoming_call_callback: IncomingCallCallback | None = None
        self._call_state_callback: CallStateCallback | None = None
        self._audio_callback: AudioCallback | None = None

        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._rfc_threads: set[threading.Thread] = set()
        self._dialog = RfcVoipDialogController()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._monitor_stop.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_call_states,
                name="Telephony SIP State Monitor",
                daemon=True,
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        error: BaseException | None = None
        try:
            self.unregister_account()
        except BaseException as exc:
            error = exc
        finally:
            with self._lock:
                self._started = False
                self._monitor_stop.set()
                monitor_thread = self._monitor_thread
                self._monitor_thread = None

            if monitor_thread is not None and monitor_thread is not threading.current_thread():
                monitor_thread.join(timeout=self._shutdown_timeout)

        if error is not None:
            raise error

    def register_account(self, config: SipAccountConfig) -> SipRegistrationState:
        with self._lock:
            if not self._started:
                raise SipEngineStateError("SipEngine.start() must be called before registering an account.")
            if self._phone is not None:
                raise SipEngineStateError("This SipEngine instance already has a registered account.")

            before_threads = set(threading.enumerate())
            audio = config.audio_format
            phone = _RfcVoipPhone(
                server=config.server,
                port=config.port,
                username=config.username,
                password=config.password,
                auth_username=config.auth_username,
                proxy=config.proxy,
                proxy_port=config.proxy_port,
                transport=config.transport,
                myIP=config.local_ip,
                tls_context=config.tls_context,
                tls_server_name=config.tls_server_name,
                callCallback=self._handle_incoming_call,
                audio_sample_rate=audio.sample_rate,
                audio_channels=audio.channels,
                audio_bit_depth=audio.bit_depth,
            )
            advertised_ip = str(config.advertised_ip or "").strip()
            if advertised_ip:
                phone.sip._telephony_advertised_ip = advertised_ip
            self._account = config
            self._phone = phone

        try:
            phone.start()
        except BaseException:
            try:
                phone.stop()
            finally:
                with self._lock:
                    self._phone = None
                    self._account = None
            raise

        self._capture_rfc_threads(before_threads)
        return self.registration_state

    def unregister_account(self) -> None:
        with self._lock:
            phone = self._phone
            tracked_threads = set(self._rfc_threads)
            if phone is not None:
                tracked_threads.update(getattr(phone, "threads", []) or [])
                register_thread = getattr(getattr(phone, "sip", None), "registerThread", None)
                if register_thread is not None:
                    tracked_threads.add(register_thread)

        if phone is None:
            return

        shutdown_error: BaseException | None = None
        try:
            phone.stop()
        finally:
            try:
                self._wait_for_threads(tracked_threads)
            except BaseException as exc:
                shutdown_error = exc
            finally:
                with self._lock:
                    self._phone = None
                    self._account = None
                    self._calls.clear()
                    self._final_states.clear()
                    self._last_states.clear()
                    self._terminal_outcomes.clear()
                    self._rfc_threads.clear()

        if shutdown_error is not None:
            raise shutdown_error

    @property
    def registration_state(self) -> SipRegistrationState:
        with self._lock:
            phone = self._phone
        if phone is None:
            return SipRegistrationState.INACTIVE
        return _REGISTRATION_STATE_MAP.get(phone.get_status(), SipRegistrationState.FAILED)

    @property
    def audio_format(self) -> TelephonyAudioFormat:
        with self._lock:
            account = self._account
        if account is None:
            return TelephonyAudioFormat()
        return account.audio_format

    def capabilities(self) -> dict[str, object]:
        return {
            "dtmf": True,
            "hold": True,
            "transfer": True,
            "attended_transfer": True,
            "media_status": True,
            "codecs": [codec.name for codec in _available_telephony_audio_codecs()],
        }

    def call_media_status(self, call_id: str) -> dict[str, object]:
        call = self._require_call(call_id).call
        clients = self._rtp_clients(call)
        client = clients[0] if clients else None
        codec = getattr(client, "preference", None) if client is not None else None
        codec_name = getattr(codec, "name", None) or (str(codec) if codec is not None else None)
        if codec_name and codec_name.startswith("PayloadType."):
            codec_name = codec_name.split(".", 1)[1]
        payload_type = getattr(client, "preference_payload_type", None) if client is not None else None
        return {
            "codec": codec_name,
            "payload_type": int(payload_type) if isinstance(payload_type, int) else payload_type,
            "rtp_streams": len(clients),
            "local_rtp_ports": [int(client.inPort) for client in clients],
            "input_buffer_bytes": self.audio_available(call_id),
            "output_buffer_bytes": self.audio_output_available(call_id),
            "public_sample_rate": self.audio_format.sample_rate,
            "public_channels": self.audio_format.channels,
            "public_bit_depth": self.audio_format.bit_depth,
        }

    def signaling_status(self) -> dict[str, object]:
        with self._lock:
            phone = self._phone
            account = self._account
        if phone is None or account is None:
            return {}
        local_port = int(getattr(getattr(phone, "sip", None), "myPort", 0) or 0)
        return {
            "local_ip": account.local_ip,
            "local_sip_port": local_port or None,
            "server": account.server,
            "server_port": account.port,
            "transport": account.transport,
        }

    def make_call(self, number: str) -> str:
        phone = self._require_registered_phone()
        call = phone.call(number)
        call_id = str(call.call_id)
        self._track_call(call_id, call, SipCallDirection.OUTGOING)
        self._publish_call_state(call_id, self._map_call_state(call.state))
        return call_id

    def answer_call(self, call_id: str) -> None:
        tracked = self._require_call(call_id)
        tracked.call.answer()
        self._publish_call_state(call_id, self._map_call_state(tracked.call.state))

    def reject_call(self, call_id: str) -> None:
        tracked = self._require_call(call_id)
        with self._lock:
            self._terminal_outcomes[call_id] = SipCallOutcome.CANCELED
        tracked.call.deny()
        self._publish_call_state(call_id, self._map_call_state(tracked.call.state))

    def hangup_call(self, call_id: str) -> None:
        tracked = self._require_call(call_id)
        state = tracked.call.state

        if state == CallState.ANSWERED:
            with self._lock:
                self._terminal_outcomes[call_id] = SipCallOutcome.COMPLETED
            tracked.call.hangup()
        elif state in (CallState.DIALING, CallState.RINGING):
            with self._lock:
                self._terminal_outcomes[call_id] = SipCallOutcome.CANCELED
            if tracked.direction == SipCallDirection.INCOMING and state == CallState.RINGING:
                tracked.call.deny()
            else:
                tracked.call.cancel()
        elif state == CallState.ENDED:
            return

        self._publish_call_state(call_id, self._map_call_state(tracked.call.state))

    def hold_call(self, call_id: str) -> None:
        tracked = self._require_call(call_id)
        self._dialog.set_hold(tracked.call, True)

    def resume_call(self, call_id: str) -> None:
        tracked = self._require_call(call_id)
        self._dialog.set_hold(tracked.call, False)

    def transfer_call(self, call_id: str, target: str) -> SipTransferResult:
        tracked = self._require_call(call_id)
        result = self._dialog.transfer(tracked.call, target)
        if result.completed and tracked.call.state == CallState.ANSWERED:
            tracked.call.hangup()
        return result

    def attended_transfer(self, call_id: str, consult_call_id: str) -> SipTransferResult:
        tracked = self._require_call(call_id)
        consult = self._require_call(consult_call_id)
        result = self._dialog.attended_transfer(tracked.call, consult.call)
        if result.completed and tracked.call.state == CallState.ANSWERED:
            # SIP.js does the same for attended REFER: terminate the REFERing
            # (original) dialog after transfer acceptance. Telephony waits for
            # the stronger final 2xx refer NOTIFY first. The consultation
            # dialog is named by Replaces and is cleared by the PBX itself.
            tracked.call.hangup()
        return result

    def send_dtmf(self, call_id: str, digits: str) -> bool:
        return bool(self._require_call(call_id).call.send_dtmf(digits))

    def read_audio(
        self,
        call_id: str,
        length: int | None = None,
        *,
        blocking: bool = True,
    ) -> bytes:
        call = self._require_call(call_id).call
        data = call.read_audio(length=length, blocking=blocking)

        with self._lock:
            callback = self._audio_callback
        if callback is not None:
            self._invoke_callback(callback, call_id, data)

        return data

    @staticmethod
    def _rtp_clients(call: VoIPCall) -> list:
        snapshot = getattr(call, "_rtp_clients_snapshot", None)
        return list(snapshot()) if callable(snapshot) else list(getattr(call, "RTPClients", ()))

    def audio_available(self, call_id: str) -> int:
        call = self._require_call(call_id).call
        clients = self._rtp_clients(call)
        if not clients:
            return 0
        available = []
        for client in clients:
            manager = getattr(client, "pmin", None)
            getter = getattr(manager, "available", None)
            if not callable(getter):
                return 0
            available.append(int(getter()))
        return min(available) if available else 0

    def audio_output_available(self, call_id: str) -> int:
        call = self._require_call(call_id).call
        clients = self._rtp_clients(call)
        if not clients:
            return 0
        available = []
        for client in clients:
            manager = getattr(client, "pmout", None)
            getter = getattr(manager, "available", None)
            if not callable(getter):
                return 0
            available.append(int(getter()))
        return min(available) if available else 0

    @staticmethod
    def _realign_rtp_write_cursor(call: VoIPCall) -> None:
        """Keep RFCVoIP's write cursor at or ahead of its RTP transmitter cursor.

        RFCVoIP's RTP transmitter advances its read cursor every packet interval and
        emits silence when no application audio is queued. If browser media stalls,
        ``outOffset`` can otherwise remain behind that read cursor and later speech is
        written into already-consumed time. Asterisk avoids this with one channel
        timer; realigning here gives RFCVoIP the same FIFO semantics.
        """
        for client in RfcVoipEngine._rtp_clients(call):
            manager = getattr(client, "pmout", None)
            lock = getattr(manager, "bufferLock", None)
            buffer = getattr(manager, "buffer", None)
            if manager is None or lock is None or buffer is None or not hasattr(client, "outOffset"):
                continue
            with lock:
                logical_read_offset = int(getattr(manager, "offset", 0)) + int(buffer.tell())
                if int(client.outOffset) < logical_read_offset:
                    client.outOffset = logical_read_offset

    def write_audio(self, call_id: str, data: bytes) -> None:
        call = self._require_call(call_id).call
        self._realign_rtp_write_cursor(call)
        call.write_audio(data)

    def get_call_state(self, call_id: str) -> SipCallState:
        with self._lock:
            tracked = self._calls.get(call_id)
            final_state = self._final_states.get(call_id)

        if tracked is not None:
            return self._map_call_state(tracked.call.state)
        if final_state is not None:
            return final_state
        raise SipCallNotFoundError(f"Unknown SIP call: {call_id}")

    def terminal_outcome(self, call_id: str) -> SipCallOutcome | None:
        with self._lock:
            return self._terminal_outcomes.get(call_id)

    def on_incoming_call(self, callback: IncomingCallCallback | None) -> None:
        with self._lock:
            self._incoming_call_callback = callback

    def on_call_state(self, callback: CallStateCallback | None) -> None:
        with self._lock:
            self._call_state_callback = callback

    def on_audio(self, callback: AudioCallback | None) -> None:
        with self._lock:
            self._audio_callback = callback

    def _require_registered_phone(self) -> VoIPPhone:
        with self._lock:
            phone = self._phone
        if phone is None or phone.get_status() != PhoneStatus.REGISTERED:
            raise SipEngineStateError("No SIP account is currently registered.")
        return phone

    def _require_call(self, call_id: str) -> _TrackedCall:
        with self._lock:
            tracked = self._calls.get(call_id)
        if tracked is None:
            raise SipCallNotFoundError(f"Unknown or ended SIP call: {call_id}")
        return tracked

    def _track_call(
        self,
        call_id: str,
        call: VoIPCall,
        direction: SipCallDirection,
    ) -> None:
        with self._lock:
            self._calls[call_id] = _TrackedCall(call=call, direction=direction)
            self._final_states.pop(call_id, None)
            self._last_states.pop(call_id, None)
            self._terminal_outcomes.pop(call_id, None)

    def _handle_incoming_call(self, call: VoIPCall) -> None:
        call_id = str(call.call_id)
        self._track_call(call_id, call, SipCallDirection.INCOMING)

        headers = getattr(getattr(call, "request", None), "headers", {}) or {}
        source = headers.get("From", {}) if isinstance(headers, dict) else {}
        target = headers.get("To", {}) if isinstance(headers, dict) else {}

        incoming = SipIncomingCall(
            call_id=call_id,
            caller_id=self._header_number(source),
            called_number=self._header_number(target),
            caller_name=self._header_display_name(source),
            ivr_route=self._header_text(headers, "X-Frappe-IVR-Route"),
        )

        with self._lock:
            callback = self._incoming_call_callback
        if callback is not None:
            self._invoke_callback(callback, incoming)

        self._publish_call_state(call_id, self._map_call_state(call.state))

    def _monitor_call_states(self) -> None:
        while not self._monitor_stop.wait(self._state_poll_interval):
            with self._lock:
                snapshot = list(self._calls.items())

            for call_id, tracked in snapshot:
                state = self._map_call_state(tracked.call.state)
                self._publish_call_state(call_id, state)
                if state == SipCallState.ENDED:
                    with self._lock:
                        current = self._calls.get(call_id)
                        if current is tracked:
                            self._calls.pop(call_id, None)
                            self._final_states[call_id] = state

    def _publish_call_state(self, call_id: str, state: SipCallState) -> None:
        with self._lock:
            previous = self._last_states.get(call_id)
            if previous == state:
                return
            if state == SipCallState.ENDED and call_id not in self._terminal_outcomes:
                self._terminal_outcomes[call_id] = self._infer_terminal_outcome_locked(call_id, previous)
            self._last_states[call_id] = state
            callback = self._call_state_callback

        if callback is not None:
            self._invoke_callback(callback, call_id, state)

    def _infer_terminal_outcome_locked(
        self, call_id: str, previous: SipCallState | None
    ) -> SipCallOutcome:
        if previous == SipCallState.CONNECTED:
            return SipCallOutcome.COMPLETED

        tracked = self._calls.get(call_id)
        if tracked is not None and tracked.direction == SipCallDirection.INCOMING:
            return SipCallOutcome.NO_ANSWER

        phone = self._phone
        status_code = phone.final_invite_status(call_id) if isinstance(phone, _RfcVoipPhone) else None
        if status_code in {486, 600}:
            return SipCallOutcome.BUSY
        if status_code in {408, 480}:
            return SipCallOutcome.NO_ANSWER
        if status_code in {487, 603}:
            return SipCallOutcome.CANCELED
        if status_code is not None:
            return SipCallOutcome.FAILED
        if previous == SipCallState.RINGING:
            return SipCallOutcome.NO_ANSWER
        return SipCallOutcome.FAILED

    def _capture_rfc_threads(self, before_threads: set[threading.Thread]) -> None:
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            current = set(threading.enumerate())
            new_threads = current - before_threads
            matching = {
                thread
                for thread in new_threads
                if thread.name == "SIP Receive" or thread.name.startswith("SIP Register CSeq:")
            }
            if matching:
                with self._lock:
                    self._rfc_threads.update(matching)
            if any(thread.name == "SIP Receive" for thread in matching):
                return
            time.sleep(0.005)

    def _wait_for_threads(self, threads: set[threading.Thread]) -> None:
        deadline = time.monotonic() + self._shutdown_timeout
        current_thread = threading.current_thread()

        for thread in threads:
            if thread is current_thread or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

        alive = [thread.name for thread in threads if thread is not current_thread and thread.is_alive()]
        if alive:
            raise SipEngineShutdownTimeout(
                "RFCVoIP threads did not stop within "
                f"{self._shutdown_timeout:.2f}s: {', '.join(sorted(alive))}"
            )

    @staticmethod
    def _map_call_state(state: CallState) -> SipCallState:
        return _CALL_STATE_MAP.get(state, SipCallState.ENDED)

    @staticmethod
    def _header_number(header) -> str | None:
        if isinstance(header, dict):
            value = header.get("number")
            return str(value).strip() if value not in (None, "") else None
        return None

    @staticmethod
    def _header_display_name(header) -> str | None:
        if isinstance(header, dict):
            value = header.get("caller")
            return str(value).strip() if value not in (None, "") else None
        return None

    @staticmethod
    def _header_text(headers, name: str) -> str | None:
        if not isinstance(headers, dict):
            return None
        value = None
        wanted = name.lower()
        for key, candidate in headers.items():
            if str(key).lower() == wanted:
                value = candidate
                break
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value in (None, ""):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _invoke_callback(callback, *args) -> None:
        try:
            callback(*args)
        except Exception:
            logger.exception("Telephony SIP callback failed")
