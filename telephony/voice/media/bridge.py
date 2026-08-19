from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from telephony.voice.media.transport import MEDIA_FLOW_XOFF, MEDIA_FLOW_XON, PcmChunkError, PcmChunkFramer
from telephony.voice.sip import SipCallNotFoundError, SipCallState, SipEngine, TelephonyAudioFormat


class MediaBridgeError(RuntimeError):
    pass


class MediaBridgeStateError(MediaBridgeError):
    pass


class MediaBridgeFrameError(MediaBridgeError):
    pass


class MediaBridgeShutdownTimeout(MediaBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class PcmDownlinkFrame:
    """One canonical PCM frame positioned on Telephony's 16 kHz media clock."""

    sequence: int
    timestamp: int
    pcm: bytes


@dataclass(frozen=True, slots=True)
class PcmMediaBridgeStats:
    browser_chunks_received: int
    browser_frames_received: int
    browser_frames_dropped: int
    sip_frames_written: int
    sip_frames_read: int
    sip_frames_dropped: int
    browser_frames_sent: int
    sip_null_ticks: int
    browser_partial_bytes: int
    flow_xoff_events: int
    flow_xon_events: int


class PcmMediaBridge:
    """Bounded, real-time PCM bridge between a browser media session and SipEngine."""

    def __init__(
        self,
        engine: SipEngine,
        call_id: str,
        send_to_browser: Callable[[PcmDownlinkFrame], None],
        *,
        max_queue_frames: int = 10,
        shutdown_timeout: float = 1.0,
        on_browser_frame_written: Callable[[bytes], None] | None = None,
        on_sip_frame_sent: Callable[[bytes], None] | None = None,
        on_flow_control: Callable[[str, int], None] | None = None,
        flow_high_watermark_frames: int | None = None,
        flow_low_watermark_frames: int | None = None,
    ):
        self.engine = engine
        self.call_id = call_id
        self.send_to_browser = send_to_browser
        self.on_browser_frame_written = on_browser_frame_written
        self.on_sip_frame_sent = on_sip_frame_sent
        self.on_flow_control = on_flow_control
        self.audio_format = engine.audio_format

        if self.audio_format != TelephonyAudioFormat():
            raise MediaBridgeStateError(
                "Telephony PCM bridge requires PCM16 mono, 16 kHz, 20 ms frames."
            )

        queue_frames = max(1, int(max_queue_frames))
        self._browser_to_sip: queue.Queue[bytes] = queue.Queue(maxsize=queue_frames)
        self._sip_to_browser: queue.Queue[PcmDownlinkFrame] = queue.Queue(maxsize=queue_frames)
        self._framer = PcmChunkFramer(self.audio_format.frame_size)
        high = (
            flow_high_watermark_frames
            if flow_high_watermark_frames is not None
            else max(1, (queue_frames * 9 + 9) // 10)
        )
        low = (
            flow_low_watermark_frames
            if flow_low_watermark_frames is not None
            else max(0, round(0.040 / (self.audio_format.frame_ms / 1000.0)))
        )
        self._flow_high_watermark = min(queue_frames, max(1, int(high)))
        self._flow_low_watermark = min(self._flow_high_watermark - 1, max(0, int(low)))
        self._flow_paused = False
        self._shutdown_timeout = max(0.1, float(shutdown_timeout))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._held = False

        self._browser_chunks_received = 0
        self._browser_frames_received = 0
        self._browser_frames_dropped = 0
        self._sip_frames_written = 0
        self._sip_frames_read = 0
        self._sip_frames_dropped = 0
        self._browser_frames_sent = 0
        self._sip_null_ticks = 0
        self._flow_xoff_events = 0
        self._flow_xon_events = 0
        self._sip_media_sequence = 0
        self._sip_media_timestamp = 0

    @property
    def frame_size(self) -> int:
        return self.audio_format.frame_size

    @property
    def frame_samples(self) -> int:
        return round(self.audio_format.sample_rate * self.audio_format.frame_ms / 1000)

    def _advance_sip_media_clock(self) -> tuple[int, int]:
        with self._lock:
            sequence = self._sip_media_sequence
            timestamp = self._sip_media_timestamp
            self._sip_media_sequence = (sequence + 1) & 0xFFFFFFFF
            self._sip_media_timestamp = (timestamp + self.frame_samples) & 0xFFFFFFFF
        return sequence, timestamp

    def _stamp_sip_frame(self, frame: bytes) -> PcmDownlinkFrame:
        sequence, timestamp = self._advance_sip_media_clock()
        return PcmDownlinkFrame(sequence=sequence, timestamp=timestamp, pcm=bytes(frame))

    @property
    def running(self) -> bool:
        return self._running and not self._stop.is_set()

    @property
    def held(self) -> bool:
        with self._lock:
            return self._held

    def set_held(self, held: bool) -> None:
        with self._lock:
            self._held = bool(held)
            self._framer.clear()
        self._clear_queue(self._browser_to_sip)
        self._clear_queue(self._sip_to_browser)
        self._update_flow_control(force_resume=True)

    @property
    def stats(self) -> PcmMediaBridgeStats:
        with self._lock:
            return PcmMediaBridgeStats(
                browser_chunks_received=self._browser_chunks_received,
                browser_frames_received=self._browser_frames_received,
                browser_frames_dropped=self._browser_frames_dropped,
                sip_frames_written=self._sip_frames_written,
                sip_frames_read=self._sip_frames_read,
                sip_frames_dropped=self._sip_frames_dropped,
                browser_frames_sent=self._browser_frames_sent,
                sip_null_ticks=self._sip_null_ticks,
                browser_partial_bytes=self._framer.buffered_bytes,
                flow_xoff_events=self._flow_xoff_events,
                flow_xon_events=self._flow_xon_events,
            )

    def status(self) -> dict[str, int | bool]:
        stats = self.stats
        with self._lock:
            flow_paused = self._flow_paused
        return {
            **{name: getattr(stats, name) for name in stats.__dataclass_fields__},
            "browser_queue_length": self._browser_to_sip.qsize(),
            "browser_queue_max": self._browser_to_sip.maxsize,
            "browser_queue_xoff": self._flow_high_watermark,
            "browser_queue_xon": self._flow_low_watermark,
            "browser_flow_paused": flow_paused,
            "sip_queue_length": self._sip_to_browser.qsize(),
            "sip_queue_max": self._sip_to_browser.maxsize,
            "running": self.running,
            "held": self.held,
        }

    def start(self) -> None:
        if self.running:
            return
        if self.engine.get_call_state(self.call_id) != SipCallState.CONNECTED:
            raise MediaBridgeStateError("PCM media bridge requires a connected SIP call.")

        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._write_to_sip, name="Telephony PCM Browser to SIP", daemon=True),
            threading.Thread(target=self._read_from_sip, name="Telephony PCM SIP Reader", daemon=True),
            threading.Thread(target=self._send_browser_audio, name="Telephony PCM SIP to Browser", daemon=True),
        ]
        self._running = True
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        deadline = time.monotonic() + self._shutdown_timeout
        for thread in list(self._threads):
            if thread is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

        alive = [thread.name for thread in self._threads if thread.is_alive()]
        self._threads.clear()
        self._running = False
        if alive:
            raise MediaBridgeShutdownTimeout(
                "PCM media bridge threads did not stop: " + ", ".join(alive)
            )

    def push_browser_audio(self, chunk: bytes) -> None:
        if not self.running:
            raise MediaBridgeStateError("PCM media bridge is not running.")
        chunk = bytes(chunk)
        try:
            with self._lock:
                if self._held:
                    return
                self._browser_chunks_received += 1
                frames = self._framer.feed(chunk)
        except PcmChunkError as exc:
            raise MediaBridgeFrameError(str(exc)) from exc

        for frame in frames:
            with self._lock:
                self._browser_frames_received += 1
            if self._put_latest(self._browser_to_sip, frame):
                with self._lock:
                    self._browser_frames_dropped += 1
        self._update_flow_control()

    def _write_to_sip(self) -> None:
        """Feed the SIP engine FIFO; the RTP engine owns the only media clock.

        This mirrors Asterisk chan_websocket: WebSocket arrival timing only fills a
        queue, while the telephony engine's media timer determines packet pacing.
        Keep roughly 40 ms queued ahead, then let backpressure build in this bridge
        where XOFF/XON can control the browser.
        """
        frame_seconds = self.audio_format.frame_ms / 1000.0
        target_bytes = self.frame_size * max(1, round(0.040 / frame_seconds))
        output_available = getattr(self.engine, "audio_output_available", None)

        while not self._stop.is_set():
            if self.held:
                self._stop.wait(frame_seconds)
                continue

            try:
                buffered = output_available(self.call_id) if callable(output_available) else None
            except (SipCallNotFoundError, RuntimeError):
                self._stop.set()
                return

            if buffered is not None and buffered >= target_bytes:
                self._update_flow_control()
                self._stop.wait(min(0.005, frame_seconds / 2))
                continue

            try:
                frame = self._browser_to_sip.get(timeout=frame_seconds)
            except queue.Empty:
                # Do not synthesize a second media clock here. RFCVoIP's RTP
                # transmitter emits silence when its FIFO is empty.
                self._update_flow_control()
                continue

            self._update_flow_control()
            if self.held:
                continue
            try:
                self.engine.write_audio(self.call_id, frame)
            except (SipCallNotFoundError, RuntimeError):
                self._stop.set()
                return
            with self._lock:
                self._sip_frames_written += 1
            self._observe(self.on_browser_frame_written, frame)

    def _read_from_sip(self) -> None:
        availability = getattr(self.engine, "audio_available", None)
        frame_seconds = self.audio_format.frame_ms / 1000.0
        max_jitter_frames = max(1, round(0.200 / frame_seconds))
        target_frames = max(1, round(0.040 / frame_seconds))

        while not self._stop.is_set():
            try:
                if self.engine.get_call_state(self.call_id) != SipCallState.CONNECTED:
                    self._stop.set()
                    return

                buffered = availability(self.call_id) if callable(availability) else None
                if buffered is not None:
                    # Asterisk forwards outbound WebSocket media when the core produces
                    # voice frames; it doesn't add another channel timer here. Drain
                    # real RTP media promptly and let the browser jitter buffer pace it.
                    while buffered > max_jitter_frames * self.frame_size:
                        dropped = self.engine.read_audio(self.call_id, self.frame_size, blocking=False)
                        if len(dropped) != self.frame_size:
                            break
                        # Preserve media time even when stale receive backlog is
                        # intentionally discarded. The browser can conceal this
                        # timestamp/sequence gap instead of silently compressing speech.
                        self._advance_sip_media_clock()
                        with self._lock:
                            self._sip_frames_dropped += 1
                        buffered = availability(self.call_id)
                        if buffered <= target_frames * self.frame_size:
                            break
                    if buffered < self.frame_size:
                        with self._lock:
                            self._sip_null_ticks += 1
                        self._stop.wait(0.002)
                        continue
                else:
                    # Generic-engine fallback when real receive-buffer availability is
                    # unknown. RFCVoIP uses the immediate path above.
                    self._stop.wait(frame_seconds)

                frame = self.engine.read_audio(
                    self.call_id,
                    self.frame_size,
                    blocking=False,
                )
            except (SipCallNotFoundError, RuntimeError):
                self._stop.set()
                return

            if len(frame) != self.frame_size:
                with self._lock:
                    self._sip_null_ticks += 1
                continue

            timed_frame = self._stamp_sip_frame(frame)
            with self._lock:
                self._sip_frames_read += 1
            if not self.held and self._put_latest(self._sip_to_browser, timed_frame):
                with self._lock:
                    self._sip_frames_dropped += 1

    def _send_browser_audio(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._sip_to_browser.get(timeout=0.05)
            except queue.Empty:
                continue
            if self.held:
                continue
            try:
                self.send_to_browser(frame)
            except Exception:
                self._stop.set()
                return
            with self._lock:
                self._browser_frames_sent += 1
            self._observe(self.on_sip_frame_sent, frame.pcm)

    def _update_flow_control(self, *, force_resume: bool = False) -> None:
        queue_length = self._browser_to_sip.qsize()
        event: str | None = None
        with self._lock:
            if force_resume and self._flow_paused:
                self._flow_paused = False
                self._flow_xon_events += 1
                event = MEDIA_FLOW_XON
            elif not self._flow_paused and queue_length >= self._flow_high_watermark:
                self._flow_paused = True
                self._flow_xoff_events += 1
                event = MEDIA_FLOW_XOFF
            elif self._flow_paused and queue_length <= self._flow_low_watermark:
                self._flow_paused = False
                self._flow_xon_events += 1
                event = MEDIA_FLOW_XON
        if event is not None and self.on_flow_control is not None:
            try:
                self.on_flow_control(event, queue_length)
            except Exception:
                pass

    @staticmethod
    def _observe(callback: Callable[[bytes], None] | None, frame: bytes) -> None:
        if callback is None:
            return
        try:
            callback(frame)
        except Exception:
            return

    @staticmethod
    def _clear_queue(target: queue.Queue[bytes]) -> None:
        while True:
            try:
                target.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _put_latest(target: queue.Queue[bytes], frame: bytes) -> bool:
        dropped = False
        try:
            target.put_nowait(frame)
            return dropped
        except queue.Full:
            pass

        try:
            target.get_nowait()
            dropped = True
        except queue.Empty:
            pass
        try:
            target.put_nowait(frame)
        except queue.Full:
            dropped = True
        return dropped
