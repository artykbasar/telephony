from __future__ import annotations

import logging
import select
import time
import warnings

from rfcvoip.RTP import RTPClient, RTPParseError, TransmitType

logger = logging.getLogger(__name__)


_MAX_CLOCK_LATE_SECONDS = 0.060
_RX_SELECT_TIMEOUT_SECONDS = 0.050


def _telephony_rtp_recv(self: RTPClient) -> None:
    """Receive RTP on socket readiness instead of RFCVoIP's 10 ms poll."""
    while self.NSD:
        try:
            with self._socket_lock:
                sin = getattr(self, "sin", None)
            if sin is None:
                time.sleep(0.001)
                continue

            readable, _, _ = select.select([sin], [], [], _RX_SELECT_TIMEOUT_SECONDS)
            if not readable:
                continue
            while self.NSD:
                try:
                    packet = sin.recv(8192)
                except BlockingIOError:
                    break
                self.parse_packet(packet)
        except RTPParseError as exc:
            logger.debug("RTP packet parse failed: %s", exc)
        except (OSError, ValueError):
            if self.NSD:
                time.sleep(0.001)


def _telephony_rtp_trans(self: RTPClient) -> None:
    """Transmit against absolute media deadlines so oversleep cannot drift."""
    next_deadline = time.monotonic()

    while self.NSD:
        if self.sendrecv in (TransmitType.RECVONLY, TransmitType.INACTIVE):
            time.sleep(0.020)
            next_deadline = time.monotonic()
            continue

        with self._dtmf_lock:
            pending_dtmf = self._pending_dtmf.popleft() if self._pending_dtmf else None
        if pending_dtmf is not None:
            self.transmit_dtmf(pending_dtmf)
            next_deadline = time.monotonic()
            continue
        now = time.monotonic()
        remaining = next_deadline - now
        if remaining > 0:
            time.sleep(remaining)
        elif -remaining > _MAX_CLOCK_LATE_SECONDS:
            next_deadline = now

        adapter = self._codec_adapter(self.preference, self.preference_payload_type)
        raw_payload = self.pmout.read(adapter.source_frame_size())
        try:
            payload = adapter.encode(raw_payload)
        except Exception as exc:
            warnings.warn(
                f"RTP audio encode failed for {self.preference}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            next_deadline = time.monotonic() + 0.020
            continue

        timestamp = self.outTimestamp & 0xFFFFFFFF
        self._send_rtp_packet(
            self.preference_payload_type,
            payload,
            marker=False,
            timestamp=timestamp,
        )
        self.outTimestamp = (
            self.outTimestamp + adapter.rtp_timestamp_increment(raw_payload, payload)
        ) & 0xFFFFFFFF
        next_deadline += adapter.packet_duration_seconds(raw_payload)


def install_rfcvoip_timing_compatibility() -> None:
    """Install Telephony's RFCVoIP 2.10.x RTP scheduling compatibility shim."""
    if getattr(RTPClient, "_telephony_timing_compatibility", False):
        return

    RTPClient.recv = _telephony_rtp_recv
    RTPClient.trans = _telephony_rtp_trans
    RTPClient._telephony_timing_compatibility = True


__all__ = ["install_rfcvoip_timing_compatibility"]
