from __future__ import annotations

import unittest

from telephony.voice.media.transport import (
    MEDIA_CLOCK_HZ,
    MEDIA_FRAME_HEADER_BYTES,
    TELEPHONY_MEDIA_PROTOCOL_VERSION,
    MediaTransportCapabilities,
    pack_downlink_media_frame,
    unpack_downlink_media_frame,
)


class MediaTransportTest(unittest.TestCase):
    def test_timestamped_downlink_frame_round_trip(self):
        pcm = bytes(range(256)) * 2 + bytes(range(128))
        self.assertEqual(len(pcm), 640)
        packet = pack_downlink_media_frame(pcm, sequence=41, timestamp=320 * 41)
        self.assertEqual(len(packet), MEDIA_FRAME_HEADER_BYTES + len(pcm))
        sequence, timestamp, unpacked = unpack_downlink_media_frame(packet)
        self.assertEqual(sequence, 41)
        self.assertEqual(timestamp, 320 * 41)
        self.assertEqual(unpacked, pcm)

    def test_capabilities_describe_media_clock_and_framing(self):
        media = MediaTransportCapabilities().as_dict()
        self.assertEqual(media["protocol_version"], TELEPHONY_MEDIA_PROTOCOL_VERSION)
        self.assertEqual(media["output_framing"], "TPM1")
        self.assertEqual(media["output_header_bytes"], MEDIA_FRAME_HEADER_BYTES)
        self.assertEqual(media["media_clock_hz"], MEDIA_CLOCK_HZ)
        self.assertTrue(media["timestamped_output"])


if __name__ == "__main__":
    unittest.main()
