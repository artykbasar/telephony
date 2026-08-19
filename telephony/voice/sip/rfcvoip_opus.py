from __future__ import annotations

from opuslib_next._loader import load_libopus
from rfcvoip.codecs import opus as rfcvoip_opus


def install_bundled_opus_compatibility() -> str:
    """Make RFCVoIP use the libopus bundled with Telephony's Python dependency."""
    handle = getattr(rfcvoip_opus, "_LIBOPUS_ENCODE_HANDLE", None)
    if getattr(rfcvoip_opus, "_telephony_bundled_opus", False) and handle is not None:
        return str(getattr(handle, "_name", ""))

    handle = load_libopus()
    rfcvoip_opus._prepare_libopus_encoder_api(handle)
    rfcvoip_opus._LIBOPUS_ENCODE_HANDLE = handle
    rfcvoip_opus._LIBOPUS_AVAILABILITY = None
    rfcvoip_opus._telephony_bundled_opus = True
    return str(getattr(handle, "_name", ""))


__all__ = ["install_bundled_opus_compatibility"]
