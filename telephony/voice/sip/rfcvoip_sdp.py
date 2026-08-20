from __future__ import annotations

import ipaddress
from typing import Callable

from rfcvoip import SIP


def _usable_ip(value: object) -> str:
    raw = str(value or "").strip().strip("[]")
    if not raw:
        return ""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return ""
    return "" if address.is_unspecified else str(address)


def _learn_advertised_ip_from_register_response(client, message) -> str:
    current = _usable_ip(getattr(client, "_telephony_advertised_ip", ""))
    source = str(getattr(client, "_telephony_advertised_ip_source", "") or "")
    if current and source == "configured":
        return current
    if getattr(message, "type", None) == SIP.SIPMessageType.MESSAGE:
        return ""
    cseq = getattr(message, "headers", {}).get("CSeq", {})
    if not isinstance(cseq, dict) or str(cseq.get("method") or "").upper() != "REGISTER":
        return ""
    try:
        status = int(getattr(message, "status", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if status != int(SIP.SIPStatus.OK):
        return ""
    vias = getattr(message, "headers", {}).get("Via", [])
    if not isinstance(vias, list) or not vias or not isinstance(vias[0], dict):
        return ""
    observed = _usable_ip(vias[0].get("received"))
    if not observed:
        return current
    client._telephony_advertised_ip = observed
    client._telephony_advertised_ip_source = "register-received"
    return observed


def effective_advertised_ip(client, bind_ip: str) -> tuple[str, str]:
    advertised = _usable_ip(getattr(client, "_telephony_advertised_ip", ""))
    if advertised:
        source = str(getattr(client, "_telephony_advertised_ip_source", "") or "configured")
        return advertised, source
    bound = _usable_ip(bind_ip)
    if bound:
        return bound, "bind"
    return "", "unresolved"


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
    original_parse_message = SIP.SIPClient.parse_message

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

    def parse_message(client, message):
        _learn_advertised_ip_from_register_response(client, message)
        return original_parse_message(client, message)

    SIP.SIPClient.gen_invite = wrap_message(original_invite)
    SIP.SIPClient.gen_answer = wrap_message(original_answer)
    SIP.SIPClient._gen_options_sdp_body = options_body
    SIP.SIPClient.parse_message = parse_message
    _INSTALLED = True


__all__ = [
    "_learn_advertised_ip_from_register_response",
    "_rewrite_sdp_body",
    "_rewrite_sip_sdp",
    "effective_advertised_ip",
    "install_rfcvoip_sdp_advertised_ip_compatibility",
]
