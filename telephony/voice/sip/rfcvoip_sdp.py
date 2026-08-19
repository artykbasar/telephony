from __future__ import annotations

from typing import Callable

from rfcvoip import SIP


_INSTALLED = False


def _rewrite_sdp_body(body: str, advertised_ip: str) -> str:
    if not advertised_ip:
        return body
    address_type = SIP.SIPClient._sdp_address_type(advertised_ip)
    rewritten: list[str] = []
    for line in body.split("\r\n"):
        if line.startswith("o="):
            parts = line.split()
            if len(parts) >= 6:
                parts[-2] = address_type
                parts[-1] = advertised_ip
                line = " ".join(parts)
        elif line.startswith("c=IN "):
            parts = line.split()
            if len(parts) >= 3:
                parts[1] = address_type
                parts[2] = advertised_ip
                line = " ".join(parts)
        rewritten.append(line)
    return "\r\n".join(rewritten)


def _rewrite_sip_sdp(message: str, advertised_ip: str) -> str:
    if not advertised_ip or "\r\n\r\n" not in message:
        return message
    headers, body = message.split("\r\n\r\n", 1)
    new_body = _rewrite_sdp_body(body, advertised_ip)
    if new_body == body:
        return message
    header_lines = headers.split("\r\n")
    content_length = len(new_body.encode("utf8"))
    for index, line in enumerate(header_lines):
        if line.lower().startswith("content-length:"):
            header_lines[index] = f"Content-Length: {content_length}"
            break
    return "\r\n".join(header_lines) + "\r\n\r\n" + new_body


def install_rfcvoip_sdp_advertised_ip_compatibility() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_invite = SIP.SIPClient.gen_invite
    original_answer = SIP.SIPClient.gen_answer
    original_options = SIP.SIPClient._gen_options_sdp_body

    def wrap_message(original: Callable):
        def wrapped(client, *args, **kwargs):
            message = original(client, *args, **kwargs)
            advertised_ip = str(getattr(client, "_telephony_advertised_ip", "") or "")
            return _rewrite_sip_sdp(message, advertised_ip)
        return wrapped

    def options_body(client, *args, **kwargs):
        body = original_options(client, *args, **kwargs)
        advertised_ip = str(getattr(client, "_telephony_advertised_ip", "") or "")
        return _rewrite_sdp_body(body, advertised_ip)

    SIP.SIPClient.gen_invite = wrap_message(original_invite)
    SIP.SIPClient.gen_answer = wrap_message(original_answer)
    SIP.SIPClient._gen_options_sdp_body = options_body
    _INSTALLED = True


__all__ = [
    "_rewrite_sdp_body",
    "_rewrite_sip_sdp",
    "install_rfcvoip_sdp_advertised_ip_compatibility",
]
