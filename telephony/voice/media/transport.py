from __future__ import annotations

import struct
from dataclasses import dataclass

TELEPHONY_MEDIA_PROTOCOL_VERSION = 3
MAX_MEDIA_CHUNK_BYTES = 65_500
MEDIA_FLOW_XOFF = "xoff"
MEDIA_FLOW_XON = "xon"
MEDIA_FRAME_MAGIC = b"TPM1"
MEDIA_FRAME_HEADER = struct.Struct("!4sII")
MEDIA_FRAME_HEADER_BYTES = MEDIA_FRAME_HEADER.size
MEDIA_CLOCK_HZ = 16_000


def pack_downlink_media_frame(frame: bytes, *, sequence: int, timestamp: int) -> bytes:
    """Add an RTP-like sequence/timestamp header to browser-bound PCM."""
    return MEDIA_FRAME_HEADER.pack(
        MEDIA_FRAME_MAGIC,
        int(sequence) & 0xFFFFFFFF,
        int(timestamp) & 0xFFFFFFFF,
    ) + bytes(frame)


def unpack_downlink_media_frame(packet: bytes) -> tuple[int, int, bytes]:
    packet = bytes(packet)
    if len(packet) < MEDIA_FRAME_HEADER_BYTES:
        raise PcmChunkError("Telephony downlink media frame is shorter than its header.")
    magic, sequence, timestamp = MEDIA_FRAME_HEADER.unpack_from(packet)
    if magic != MEDIA_FRAME_MAGIC:
        raise PcmChunkError("Telephony downlink media frame has an invalid magic value.")
    return sequence, timestamp, packet[MEDIA_FRAME_HEADER_BYTES:]


class PcmChunkError(ValueError):
    pass


class PcmChunkFramer:
    """Re-frame variable PCM16 chunks into fixed canonical media frames."""

    def __init__(self, frame_size: int, *, max_chunk_bytes: int = MAX_MEDIA_CHUNK_BYTES):
        self.frame_size = int(frame_size)
        self.max_chunk_bytes = int(max_chunk_bytes)
        if self.frame_size <= 0 or self.frame_size % 2:
            raise ValueError("PCM frame size must be a positive number of 16-bit samples.")
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)
    def clear(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes) -> list[bytes]:
        chunk = bytes(chunk)
        if not chunk:
            return []
        if len(chunk) > self.max_chunk_bytes:
            raise PcmChunkError("PCM media chunk exceeds the transport maximum.")
        if len(chunk) % 2:
            raise PcmChunkError("PCM16 media chunks must contain complete 16-bit samples.")

        self._buffer.extend(chunk)
        frames: list[bytes] = []
        while len(self._buffer) >= self.frame_size:
            frames.append(bytes(self._buffer[: self.frame_size]))
            del self._buffer[: self.frame_size]
        return frames


@dataclass(frozen=True, slots=True)
class MediaTransportCapabilities:
    protocol_version: int = TELEPHONY_MEDIA_PROTOCOL_VERSION
    binary_media: bool = True
    variable_chunk_input: bool = True
    paced_output: bool = True
    timestamped_output: bool = True
    output_framing: str = "TPM1"
    output_header_bytes: int = MEDIA_FRAME_HEADER_BYTES
    media_clock_hz: int = MEDIA_CLOCK_HZ
    flow_control: str = "xoff-xon"
    max_chunk_bytes: int = MAX_MEDIA_CHUNK_BYTES

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "binary_media": self.binary_media,
            "variable_chunk_input": self.variable_chunk_input,
            "paced_output": self.paced_output,
            "timestamped_output": self.timestamped_output,
            "output_framing": self.output_framing,
            "output_header_bytes": self.output_header_bytes,
            "media_clock_hz": self.media_clock_hz,
            "flow_control": self.flow_control,
            "max_chunk_bytes": self.max_chunk_bytes,
        }
