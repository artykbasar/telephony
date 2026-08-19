from __future__ import annotations

import io
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rfcvoip.VoIP import CallState, PhoneStatus, VoIPPhone

from telephony.voice.sip import (
    SipAccountConfig,
    SipCallState,
    SipEngineStateError,
    SipRegistrationState,
    SipTransferResult,
    TelephonyAudioFormat,
)
from telephony.voice.sip.rfcvoip_engine import RfcVoipEngine, _RfcVoipPhone


class _FakeSip:
    registerThread = None


class _FakeRequest:
    def __init__(self, caller="12025550100", called="2099", caller_name=None, ivr_route=None):
        self.headers = {
            "From": {"number": caller, "caller": caller_name or ""},
            "To": {"number": called},
        }
        if ivr_route is not None:
            self.headers["X-Frappe-IVR-Route"] = ivr_route


class _FakeCall:
    def __init__(self, call_id: str, state=CallState.DIALING, request=None):
        self.call_id = call_id
        self.state = state
        self.request = request or _FakeRequest()
        self.written = []
        self.cancelled = False
        self.denied = False
        self.hung_up = False
        self.dtmf = []

    def answer(self):
        self.state = CallState.ANSWERED

    def deny(self):
        self.denied = True
        self.state = CallState.ENDED

    def cancel(self):
        self.cancelled = True
        self.state = CallState.ENDED

    def hangup(self):
        self.hung_up = True
        self.state = CallState.ENDED

    def send_dtmf(self, digits):
        self.dtmf.append(digits)
        return True

    def read_audio(self, length=None, blocking=True):
        del blocking
        return b"\x01\x02" * ((length or 640) // 2)

    def write_audio(self, data):
        self.written.append(data)


class _FakePhone:
    instances = []
    spawn_receive_thread = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.status = PhoneStatus.INACTIVE
        self.sip = _FakeSip()
        self.threads = []
        self.calls = {}
        self._receive_stop = threading.Event()
        self._receive_thread = None
        self.__class__.instances.append(self)

    def start(self):
        self.status = PhoneStatus.REGISTERED
        if self.__class__.spawn_receive_thread:
            self._receive_thread = threading.Thread(
                target=self._receive_stop.wait,
                name="SIP Receive",
                daemon=True,
            )
            self._receive_thread.start()

    def stop(self):
        self.status = PhoneStatus.INACTIVE
        self._receive_stop.set()

    def get_status(self):
        return self.status

    def call(self, number):
        call = _FakeCall(f"out-{number}")
        self.calls[call.call_id] = call
        return call


class RfcVoipEngineTest(unittest.TestCase):
    def setUp(self):
        _FakePhone.instances.clear()
        _FakePhone.spawn_receive_thread = False
        self.phone_patch = patch(
            "telephony.voice.sip.rfcvoip_engine._RfcVoipPhone",
            _FakePhone,
        )
        self.phone_patch.start()
        self.engine = RfcVoipEngine(state_poll_interval=0.005, shutdown_timeout=0.5)
        self.engine.start()
        self.config = SipAccountConfig(
            server="sip.example.test",
            username="2099",
            password="test-password",
            audio_format=TelephonyAudioFormat(),
        )

    def tearDown(self):
        self.engine.stop()
        self.phone_patch.stop()

    def register(self):
        state = self.engine.register_account(self.config)
        self.assertEqual(state, SipRegistrationState.REGISTERED)
        return _FakePhone.instances[-1]

    def wait_for(self, predicate, timeout=0.25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("condition was not observed before timeout")

    def test_start_is_required_before_registration(self):
        other = RfcVoipEngine()
        with self.assertRaises(SipEngineStateError):
            other.register_account(self.config)

    def test_registration_maps_config_and_audio_contract(self):
        phone = self.register()

        self.assertEqual(phone.kwargs["server"], "sip.example.test")
        self.assertEqual(phone.kwargs["username"], "2099")
        self.assertEqual(phone.kwargs["audio_sample_rate"], 16000)
        self.assertEqual(phone.kwargs["audio_channels"], 1)
        self.assertEqual(phone.kwargs["audio_bit_depth"], 16)
        self.assertNotIn("sipPort", phone.kwargs)
        self.assertNotIn("rtpPortLow", phone.kwargs)
        self.assertNotIn("rtpPortHigh", phone.kwargs)
        self.assertEqual(self.engine.audio_format.frame_size, 640)

        self.engine.unregister_account()
        self.assertEqual(self.engine.registration_state, SipRegistrationState.INACTIVE)

    def test_registration_applies_advertised_media_ip_without_changing_bind_ip(self):
        self.config = SipAccountConfig(
            server="sip.example.test",
            username="2099",
            password="test-password",
            local_ip="0.0.0.0",
            advertised_ip="192.168.1.222",
            audio_format=TelephonyAudioFormat(),
        )
        phone = self.register()
        self.assertEqual(phone.kwargs["myIP"], "0.0.0.0")
        self.assertEqual(phone.sip._telephony_advertised_ip, "192.168.1.222")

    def test_capabilities_report_media_diagnostics_and_dialog_controls(self):
        phone = self.register()
        capabilities = self.engine.capabilities()
        self.assertEqual(capabilities["codecs"], ["OPUS", "G722", "PCMU", "PCMA"])
        self.assertTrue(capabilities["dtmf"])
        self.assertTrue(capabilities["hold"])
        self.assertTrue(capabilities["transfer"])
        self.assertTrue(capabilities["attended_transfer"])
        self.assertTrue(capabilities["media_status"])
        call_id = self.engine.make_call("8299")
        status = self.engine.call_media_status(call_id)
        self.assertEqual(status["rtp_streams"], 0)
        self.assertEqual(status["input_buffer_bytes"], 0)
        self.assertEqual(status["output_buffer_bytes"], 0)
        self.assertEqual(status["public_sample_rate"], 16000)
        self.assertIs(phone.calls[call_id], self.engine._require_call(call_id).call)

    def test_outgoing_call_state_audio_and_dtmf_are_engine_owned(self):
        phone = self.register()
        states = []
        audio = []
        self.engine.on_call_state(lambda call_id, state: states.append((call_id, state)))
        self.engine.on_audio(lambda call_id, frame: audio.append((call_id, frame)))

        call_id = self.engine.make_call("8299")
        call = phone.calls[call_id]
        self.assertEqual(self.engine.get_call_state(call_id), SipCallState.DIALING)

        call.state = CallState.RINGING
        self.wait_for(lambda: (call_id, SipCallState.RINGING) in states)

        call.state = CallState.ANSWERED
        self.wait_for(lambda: (call_id, SipCallState.CONNECTED) in states)

        frame = self.engine.read_audio(call_id, 640, blocking=False)
        self.assertEqual(len(frame), 640)
        self.assertEqual(audio[-1], (call_id, frame))

        self.engine.write_audio(call_id, frame)
        self.assertEqual(call.written[-1], frame)
        self.assertTrue(self.engine.send_dtmf(call_id, "12#"))
        self.assertEqual(call.dtmf, ["12#"])

        self.engine.hangup_call(call_id)
        self.assertTrue(call.hung_up)
        self.assertEqual(self.engine.get_call_state(call_id), SipCallState.ENDED)

    def test_hold_and_transfer_delegate_to_dialog_controller(self):
        phone = self.register()
        first_id = self.engine.make_call("8299")
        second_id = self.engine.make_call("2098")
        first = phone.calls[first_id]
        second = phone.calls[second_id]
        first.state = CallState.ANSWERED
        second.state = CallState.ANSWERED
        result = SipTransferResult(
            mode="attended",
            target="sip:2098@example.test",
            accepted=True,
            completed=True,
            status_code=200,
            phrase="OK",
            replaces_call_id=second_id,
        )
        dialog = SimpleNamespace(
            set_hold=Mock(),
            transfer=Mock(return_value=result),
            attended_transfer=Mock(return_value=result),
        )
        self.engine._dialog = dialog

        self.engine.hold_call(first_id)
        self.engine.resume_call(first_id)
        blind = self.engine.transfer_call(first_id, "2098")

        self.assertIs(blind, result)
        dialog.set_hold.assert_any_call(first, True)
        dialog.set_hold.assert_any_call(first, False)
        dialog.transfer.assert_called_once_with(first, "2098")
        self.assertTrue(first.hung_up)

        first.state = CallState.ANSWERED
        first.hung_up = False
        attended = self.engine.attended_transfer(first_id, second_id)
        self.assertIs(attended, result)
        dialog.attended_transfer.assert_called_once_with(first, second)
        self.assertTrue(first.hung_up)
        self.assertFalse(second.hung_up)

    def test_rtp_write_cursor_realigns_after_transmitter_has_advanced(self):
        buffer = io.BytesIO()
        buffer.seek(1920)
        manager = SimpleNamespace(
            buffer=buffer,
            bufferLock=threading.RLock(),
            offset=1000,
        )
        client = SimpleNamespace(pmout=manager, outOffset=1640)
        call = SimpleNamespace(RTPClients=[client])

        RfcVoipEngine._realign_rtp_write_cursor(call)

        self.assertEqual(client.outOffset, 2920)

    def test_incoming_call_is_normalized_and_can_be_answered_or_rejected(self):
        phone = self.register()
        incoming = []
        self.engine.on_incoming_call(incoming.append)

        call = _FakeCall(
            "incoming-1",
            state=CallState.RINGING,
            request=_FakeRequest(caller="441234567890", called="2099", caller_name="John Smith", ivr_route="Landlord"),
        )
        phone.kwargs["callCallback"](call)

        self.assertEqual(incoming[0].call_id, "incoming-1")
        self.assertEqual(incoming[0].caller_id, "441234567890")
        self.assertEqual(incoming[0].called_number, "2099")
        self.assertEqual(incoming[0].caller_name, "John Smith")
        self.assertEqual(incoming[0].ivr_route, "Landlord")

        self.engine.answer_call("incoming-1")
        self.assertEqual(self.engine.get_call_state("incoming-1"), SipCallState.CONNECTED)

        second = _FakeCall("incoming-2", state=CallState.RINGING)
        phone.kwargs["callCallback"](second)
        self.engine.reject_call("incoming-2")
        self.assertTrue(second.denied)
        self.assertEqual(self.engine.get_call_state("incoming-2"), SipCallState.ENDED)

    def test_hangup_cancels_outgoing_ringing_and_denies_incoming_ringing(self):
        phone = self.register()

        outgoing_id = self.engine.make_call("8297")
        outgoing = phone.calls[outgoing_id]
        outgoing.state = CallState.RINGING
        self.engine.hangup_call(outgoing_id)
        self.assertTrue(outgoing.cancelled)

        incoming = _FakeCall("incoming-ringing", state=CallState.RINGING)
        phone.kwargs["callCallback"](incoming)
        self.engine.hangup_call("incoming-ringing")
        self.assertTrue(incoming.denied)

    def test_unregister_waits_for_captured_rfcvoip_receive_thread(self):
        _FakePhone.spawn_receive_thread = True
        phone = self.register()
        receive_thread = phone._receive_thread
        self.assertIsNotNone(receive_thread)
        self.assertTrue(receive_thread.is_alive())

        self.engine.unregister_account()

        self.assertFalse(receive_thread.is_alive())
        self.assertEqual(self.engine.registration_state, SipRegistrationState.INACTIVE)


class RfcVoipCodecFilterTest(unittest.TestCase):
    def test_first_adapter_offer_matches_browser_voice_codec_set(self):
        phone = _RfcVoipPhone(
            server="127.0.0.1",
            port=5060,
            username="test",
            password="test",
        )
        enabled = set(phone._prioritized_enabled_codecs())

        from rfcvoip.RTP import PayloadType

        expected = {
            PayloadType.OPUS,
            PayloadType.G722,
            PayloadType.PCMU,
            PayloadType.PCMA,
            PayloadType.EVENT,
        }
        self.assertTrue({PayloadType.OPUS, PayloadType.G722, PayloadType.PCMU, PayloadType.PCMA}.issubset(enabled))
        self.assertTrue(enabled.issubset(expected))
        self.assertEqual(phone._prioritized_enabled_codecs()[:4], [
            PayloadType.OPUS,
            PayloadType.G722,
            PayloadType.PCMU,
            PayloadType.PCMA,
        ])

    def test_opus_uses_python_bundled_native_library(self):
        from rfcvoip.codecs import opus as rfcvoip_opus

        handle = rfcvoip_opus._get_libopus_encode_handle()
        library = str(getattr(handle, "_name", ""))
        self.assertIn("opuslib_next", library)
        self.assertIn("_native", library)
        self.assertTrue(library.endswith("libopus.so") or library.endswith("libopus.dylib") or library.endswith("opus.dll"))

    def test_opus_and_g722_round_trip_telephony_pcm_frame(self):
        from rfcvoip import codecs
        from rfcvoip.RTP import PayloadType

        frame = b"\x01\x00" * 320
        for payload_type in (PayloadType.OPUS, PayloadType.G722):
            codec = codecs.create_codec(
                payload_type,
                source_sample_rate=16000,
                source_bit_depth=16,
                source_channels=1,
            )
            encoded = codec.encode(frame)
            decoded = codec.decode(encoded)
            self.assertTrue(encoded)
            self.assertEqual(len(decoded), len(frame))

    def test_call_ids_are_unique_across_fresh_phone_instances(self):
        first = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="test", password="test"
        )
        second = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="test", password="test"
        )

        first_id = first.sip.gen_call_id()
        second_id = second.sip.gen_call_id()

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(first.sip.myPort, 0)
        self.assertEqual(second.sip.myPort, 0)

    def test_incoming_video_offer_is_disabled_at_audio_only_adapter_boundary(self):
        phone = _RfcVoipPhone(
            server="127.0.0.1",
            port=5060,
            username="test",
            password="test",
        )
        request = SimpleNamespace(body={"m": [
            {"type": "audio", "port": 49170},
            {"type": "video", "port": 51372},
        ]})
        with patch.object(VoIPPhone, "_create_Call") as parent_create:
            phone._create_Call(request, 7)

        self.assertEqual(request.body["m"][0]["port"], 49170)
        self.assertEqual(request.body["m"][1]["port"], 0)
        parent_create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
