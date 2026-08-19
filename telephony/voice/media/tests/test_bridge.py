from __future__ import annotations

import threading
import time
import unittest

from telephony.voice.media.bridge import (
    MediaBridgeFrameError,
    MediaBridgeStateError,
    PcmMediaBridge,
)
from telephony.voice.media.transport import MEDIA_FLOW_XOFF, MEDIA_FLOW_XON
from telephony.voice.sip import SipCallState, TelephonyAudioFormat


class _FakeEngine:
    def __init__(self, *, audio_format=None, write_delay=0.0):
        self.audio_format = audio_format or TelephonyAudioFormat()
        self.state = SipCallState.CONNECTED
        self.written: list[bytes] = []
        self.read_frames: list[bytes] = []
        self.read_calls = 0
        self.write_delay = write_delay
        self.lock = threading.Lock()

    def get_call_state(self, _call_id):
        return self.state

    def write_audio(self, _call_id, frame):
        if self.write_delay:
            time.sleep(self.write_delay)
        with self.lock:
            self.written.append(bytes(frame))

    def audio_available(self, _call_id):
        with self.lock:
            # Match RFCVoIP RTPPacketManager.available(): total unread PCM backlog.
            return sum(len(frame) for frame in self.read_frames)

    def read_audio(self, _call_id, length=None, *, blocking=True):
        del blocking
        with self.lock:
            self.read_calls += 1
            if self.read_frames:
                return self.read_frames.pop(0)
        return b"" if length else b""


class PcmMediaBridgeTest(unittest.TestCase):
    def wait_for(self, predicate, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("condition was not observed before timeout")

    def test_requires_canonical_audio_format_and_connected_call(self):
        bad = _FakeEngine(audio_format=TelephonyAudioFormat(sample_rate=8000))
        with self.assertRaises(MediaBridgeStateError):
            PcmMediaBridge(bad, "call-1", lambda _frame: None)

        engine = _FakeEngine()
        engine.state = SipCallState.RINGING
        bridge = PcmMediaBridge(engine, "call-1", lambda _frame: None)
        with self.assertRaises(MediaBridgeStateError):
            bridge.start()

    def test_two_way_pcm_frames_flow_without_format_conversion(self):
        engine = _FakeEngine()
        sip_frame = bytes([7, 9]) * 320
        browser_frame = bytes([3, 5]) * 320
        engine.read_frames.append(sip_frame)
        received = []

        bridge = PcmMediaBridge(engine, "call-1", lambda frame: received.append(frame.pcm))
        bridge.start()
        try:
            bridge.push_browser_audio(browser_frame)
            self.wait_for(lambda: engine.written == [browser_frame])
            self.wait_for(lambda: sip_frame in received)
            stats = bridge.stats
            self.assertEqual(stats.browser_frames_received, 1)
            self.assertEqual(stats.sip_frames_written, 1)
            self.assertGreaterEqual(stats.sip_frames_read, 1)
            self.assertGreaterEqual(stats.browser_frames_sent, 1)
        finally:
            bridge.stop()

    def test_sip_reader_does_not_consume_synthetic_silence_before_real_rtp_arrives(self):
        engine = _FakeEngine()
        received = []
        bridge = PcmMediaBridge(engine, "call-1", lambda frame: received.append(frame.pcm))
        bridge.start()
        try:
            time.sleep(0.06)
            self.assertEqual(engine.read_calls, 0)
            self.assertEqual(received, [])
            real_frame = bytes([9]) * 640
            engine.read_frames.append(real_frame)
            self.wait_for(lambda: received == [real_frame])
            self.assertEqual(engine.read_calls, 1)
            self.assertGreaterEqual(bridge.stats.sip_null_ticks, 1)
        finally:
            bridge.stop()

    def test_receive_backlog_drop_preserves_sequence_and_timestamp_gap(self):
        engine = _FakeEngine()
        for value in range(12):
            engine.read_frames.append(bytes([value]) * 640)
        received = []
        bridge = PcmMediaBridge(
            engine,
            "call-1",
            received.append,
            max_queue_frames=20,
        )
        bridge.start()
        try:
            self.wait_for(lambda: len(received) >= 1, timeout=1.0)
            # A 12-frame backlog is trimmed to the 200 ms ceiling before export.
            # The first exported frame must retain the two skipped 20 ms slots.
            self.assertEqual(received[0].sequence, 2)
            self.assertEqual(received[0].timestamp, 640)
            self.assertEqual(received[0].pcm, bytes([2]) * 640)
            self.assertEqual(bridge.stats.sip_frames_dropped, 2)
        finally:
            bridge.stop()

    def test_observers_receive_only_media_that_crossed_the_bridge(self):
        engine = _FakeEngine()
        browser_observed = []
        sip_observed = []
        browser_frame = bytes([3, 5]) * 320
        sip_frame = bytes([7, 9]) * 320
        engine.read_frames.append(sip_frame)
        bridge = PcmMediaBridge(
            engine,
            "call-1",
            lambda _frame: None,
            on_browser_frame_written=browser_observed.append,
            on_sip_frame_sent=sip_observed.append,
        )
        bridge.start()
        try:
            bridge.push_browser_audio(browser_frame)
            self.wait_for(lambda: browser_observed == [browser_frame])
            self.wait_for(lambda: sip_frame in sip_observed)
            observed_before_hold = list(sip_observed)
            bridge.set_held(True)
            bridge.push_browser_audio(bytes([1]) * 640)
            engine.read_frames.append(bytes([2]) * 640)
            time.sleep(0.08)
            self.assertEqual(browser_observed, [browser_frame])
            self.assertEqual(sip_observed, observed_before_hold)
        finally:
            bridge.stop()

    def test_hold_suppresses_both_media_directions_and_resume_restores_flow(self):
        engine = _FakeEngine()
        received = []
        bridge = PcmMediaBridge(engine, "call-1", lambda frame: received.append(frame.pcm))
        bridge.start()
        try:
            bridge.set_held(True)
            self.assertTrue(bridge.held)
            bridge.push_browser_audio(bytes([1]) * 640)
            engine.read_frames.append(bytes([2]) * 640)
            time.sleep(0.08)
            self.assertEqual(engine.written, [])
            self.assertEqual(received, [])

            bridge.set_held(False)
            self.assertFalse(bridge.held)
            browser_frame = bytes([3]) * 640
            sip_frame = bytes([4]) * 640
            bridge.push_browser_audio(browser_frame)
            engine.read_frames.append(sip_frame)
            self.wait_for(lambda: engine.written == [browser_frame])
            self.wait_for(lambda: sip_frame in received)
        finally:
            bridge.stop()

    def test_variable_browser_chunks_are_reframed_without_conversion(self):
        engine = _FakeEngine()
        bridge = PcmMediaBridge(engine, "call-1", lambda _frame: None)
        bridge.start()
        try:
            first = bytes([1]) * 320
            second = bytes([2]) * 320
            bridge.push_browser_audio(first)
            self.assertEqual(engine.written, [])
            bridge.push_browser_audio(second)
            bridge.push_browser_audio(bytes([3]) * 1280)
            self.wait_for(lambda: len(engine.written) == 3, timeout=1.0)
            self.assertEqual(engine.written[0], first + second)
            self.assertEqual(engine.written[1:], [bytes([3]) * 640, bytes([3]) * 640])
            self.assertEqual(bridge.stats.browser_chunks_received, 3)
            self.assertEqual(bridge.stats.browser_frames_received, 3)
            self.assertEqual(bridge.stats.browser_partial_bytes, 0)
        finally:
            bridge.stop()

    def test_default_flow_window_uses_200ms_max_and_40ms_resume_target(self):
        bridge = PcmMediaBridge(_FakeEngine(), "call-1", lambda _frame: None)
        status = bridge.status()
        self.assertEqual(status["browser_queue_max"], 10)
        self.assertEqual(status["browser_queue_xoff"], 9)
        self.assertEqual(status["browser_queue_xon"], 2)

    def test_flow_control_emits_xoff_then_xon_as_queue_drains(self):
        engine = _FakeEngine(write_delay=0.05)
        events = []
        bridge = PcmMediaBridge(
            engine,
            "call-1",
            lambda _frame: None,
            max_queue_frames=3,
            flow_high_watermark_frames=2,
            flow_low_watermark_frames=0,
            on_flow_control=lambda event, length: events.append((event, length)),
        )
        bridge.start()
        try:
            bridge.push_browser_audio(bytes([7]) * 640 * 4)
            self.wait_for(lambda: any(event == MEDIA_FLOW_XOFF for event, _ in events))
            self.wait_for(lambda: any(event == MEDIA_FLOW_XON for event, _ in events), timeout=1.0)
            self.assertGreaterEqual(bridge.stats.flow_xoff_events, 1)
            self.assertGreaterEqual(bridge.stats.flow_xon_events, 1)
        finally:
            bridge.stop()

    def test_browser_gap_does_not_create_a_second_media_clock(self):
        engine = _FakeEngine()
        bridge = PcmMediaBridge(engine, "call-1", lambda _frame: None)
        first = bytes([11]) * 640
        second = bytes([12]) * 640
        later = bytes([13]) * 640
        bridge.start()
        try:
            bridge.push_browser_audio(first + second)
            self.wait_for(lambda: len(engine.written) >= 2)
            writes_before_gap = len(engine.written)
            time.sleep(0.07)
            self.assertEqual(len(engine.written), writes_before_gap)
            bridge.push_browser_audio(later)
            self.wait_for(lambda: later in engine.written, timeout=0.5)
        finally:
            bridge.stop()

    def test_rejects_wrong_browser_frame_size(self):
        engine = _FakeEngine()
        bridge = PcmMediaBridge(engine, "call-1", lambda _frame: None)
        bridge.start()
        try:
            with self.assertRaises(MediaBridgeFrameError):
                bridge.push_browser_audio(b"\x00" * 639)
        finally:
            bridge.stop()

    def test_browser_queue_is_bounded_and_discards_stale_audio(self):
        engine = _FakeEngine(write_delay=0.06)
        bridge = PcmMediaBridge(
            engine,
            "call-1",
            lambda _frame: None,
            max_queue_frames=2,
        )
        bridge.start()
        try:
            for value in range(8):
                bridge.push_browser_audio(bytes([value]) * 640)
            self.wait_for(lambda: len(engine.written) >= 2)
            self.assertGreater(bridge.stats.browser_frames_dropped, 0)
            non_silence = [frame for frame in engine.written if frame != b"\x00" * 640]
            self.assertEqual(non_silence[-1], bytes([7]) * 640)
        finally:
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
