from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import quote

import rfcvoip
from rfcvoip import RTP, SIP
from rfcvoip.SIPSubscription import SIPSubscription
from rfcvoip.VoIP import CallState

from telephony.voice.sip.models import SipTransferResult


_SIPFRAG_STATUS = re.compile(r"^\s*SIP/2\.0\s+(\d{3})(?:\s+([^\r\n]+))?", re.IGNORECASE | re.MULTILINE)


class RfcVoipDialogError(RuntimeError):
    pass


def _address_with_tag(header: Any, tag: str) -> str:
    if isinstance(header, dict):
        raw = str(header.get("raw") or "").strip()
        parsed_tag = str(header.get("tag") or "").strip()
    else:
        raw = str(header or "").strip()
        parsed_tag = ""
    if not raw:
        raise RfcVoipDialogError("SIP dialog address header is missing.")
    use_tag = str(tag or parsed_tag or "").strip()
    return f"{raw};tag={use_tag}" if use_tag else raw


def _dialog_headers(sip: SIP.SIPClient, request: SIP.SIPMessage) -> tuple[str, str, str, str]:
    call_id = str(request.headers.get("Call-ID") or "").strip()
    if not call_id:
        raise RfcVoipDialogError("SIP dialog Call-ID is missing.")
    local_tag = str(sip.tagLibrary.get(call_id) or "").strip()
    if not local_tag:
        raise RfcVoipDialogError("SIP dialog local tag is missing.")

    from_header = request.headers.get("From", {})
    to_header = request.headers.get("To", {})
    from_tag = str(from_header.get("tag") or "") if isinstance(from_header, dict) else ""
    if from_tag == local_tag:
        local_header, remote_header = from_header, to_header
    else:
        local_header, remote_header = to_header, from_header

    remote_tag = str(remote_header.get("tag") or "") if isinstance(remote_header, dict) else ""
    if not remote_tag:
        raise RfcVoipDialogError("SIP dialog remote tag is missing.")

    return (
        _address_with_tag(local_header, local_tag),
        _address_with_tag(remote_header, remote_tag),
        local_tag,
        remote_tag,
    )


def _media_spec(media_spec: Any) -> tuple[str, int, dict[int, RTP.PayloadType]]:
    if isinstance(media_spec, dict) and "codecs" in media_spec:
        return (
            str(media_spec.get("media_type") or "audio"),
            int(media_spec.get("port_count") or 1),
            dict(media_spec.get("codecs") or {}),
        )
    return "audio", 1, dict(media_spec or {})


def _sdp_body(call: Any, mode: RTP.TransmitType) -> str:
    sip = call.sip
    media_map = call._rtp_media_answer_map()
    if not media_map:
        raise RfcVoipDialogError("SIP call has no active RTP media to renegotiate.")

    session_id = str(call.session_id)
    next_version = int(getattr(call, "_telephony_sdp_version", int(session_id) + 2)) + 1
    call._telephony_sdp_version = next_version
    advertised_ip = str(getattr(sip, "_telephony_advertised_ip", "") or call.myIP)
    address_type = sip._sdp_address_type(advertised_ip)

    body = "v=0\r\n"
    body += f"o=rfcvoip {session_id} {next_version} IN {address_type} {advertised_ip}\r\n"
    body += f"s=rfcvoip {rfcvoip.__version__}\r\n"
    body += f"c=IN {address_type} {advertised_ip}\r\n"
    body += "t=0 0\r\n"

    for port, raw_spec in media_map.items():
        media_type, port_count, codecs = _media_spec(raw_spec)
        port_field = str(port) if port_count == 1 else f"{port}/{port_count}"
        body += f"m={media_type} {port_field} RTP/AVP"
        for payload_type in codecs:
            body += f" {payload_type}"
        body += "\r\n"
        for payload_type, codec in codecs.items():
            body += "a=rtpmap:" + RTP.rtpmap_for_payload_type(payload_type, codec) + "\r\n"
            for fmtp in RTP.fmtp_for_payload_type(payload_type, codec):
                body += f"a=fmtp:{payload_type} {fmtp}\r\n"
        body += "a=ptime:20\r\n"
        body += "a=maxptime:150\r\n"
        body += f"a={mode}\r\n"
    return body


def _sync_bye_cseq(sip: SIP.SIPClient, used_cseq: int) -> None:
    counter = getattr(sip, "byeCounter", None)
    lock = getattr(counter, "_lock", None)
    if counter is None or lock is None or not hasattr(counter, "x"):
        return
    with lock:
        if int(counter.x) <= int(used_cseq):
            counter.x = int(used_cseq) + 1


def _next_dialog_cseq(sip: SIP.SIPClient) -> int:
    cseq = int(sip.inviteCounter.next())
    _sync_bye_cseq(sip, cseq)
    return cseq


def _request_matches(response: SIP.SIPMessage, call_id: str, cseq: int, method: str) -> bool:
    response_cseq = response.headers.get("CSeq", {})
    return (
        str(response.headers.get("Call-ID") or "") == call_id
        and str(response_cseq.get("check") if isinstance(response_cseq, dict) else "") == str(cseq)
        and str(response_cseq.get("method") if isinstance(response_cseq, dict) else "").upper() == method.upper()
    )


def _auth_header(
    sip: SIP.SIPClient,
    response: SIP.SIPMessage,
    *,
    method: str,
    uri: str,
    body: bytes = b"",
) -> str:
    header_name = "Authorization"
    if (
        response.status == SIP.SIPStatus.PROXY_AUTHENTICATION_REQUIRED
        or getattr(response, "authentication_header", None) == "Proxy-Authenticate"
    ):
        header_name = "Proxy-Authorization"
    return sip._build_digest_auth_header(
        response,
        header_name=header_name,
        method=method,
        uri=uri,
        body=body,
    )


def _send_ack(sip: SIP.SIPClient, response: SIP.SIPMessage, request_uri: str) -> None:
    ack = sip.gen_ack(response, request_uri=request_uri)
    sip.send_raw(ack.encode("utf8"), sip.ack_target(response))


def _update_rtp_from_answer(call: Any, response: SIP.SIPMessage) -> None:
    media_sections = (getattr(response, "body", {}) or {}).get("m", [])
    clients = list(call._rtp_clients_snapshot())
    targets: list[tuple[str, int]] = []
    for media in media_sections:
        if not isinstance(media, dict) or media.get("type") != "audio" or not media.get("port"):
            continue
        address = media.get("connection") or (getattr(response, "body", {}) or {}).get("c")
        if isinstance(address, dict):
            host = address.get("address")
        else:
            host = None
        if not host:
            continue
        targets.append((str(host), int(media["port"])))
    if len(targets) != len(clients):
        return
    for client, (host, port) in zip(clients, targets):
        client.outIP = host
        client.outPort = port


class RfcVoipDialogController:
    def __init__(self, *, hold_timeout: float = 8.0, transfer_timeout: float = 45.0):
        self.hold_timeout = max(1.0, float(hold_timeout))
        self.transfer_timeout = max(5.0, float(transfer_timeout))
        self._refer_lock = threading.Lock()
        self._refer_attempts: dict[str, int] = {}

    def set_hold(self, call: Any, held: bool) -> None:
        if call.state != CallState.ANSWERED:
            raise RfcVoipDialogError("SIP hold requires an answered call.")
        mode = RTP.TransmitType.SENDONLY if held else RTP.TransmitType.SENDRECV
        self._reinvite(call, mode)
        call.sendmode = mode
        for client in call._rtp_clients_snapshot():
            client.sendrecv = mode

    def _build_in_dialog_request(
        self,
        call: Any,
        *,
        method: str,
        cseq: int,
        body: str = "",
        content_type: str | None = None,
        extra_headers: list[str] | None = None,
        auth_line: str = "",
    ) -> tuple[str, str]:
        sip = call.sip
        local, remote, _local_tag, _remote_tag = _dialog_headers(sip, call.request)
        remote_uri = sip._dialog_remote_uri(call.request)
        if not remote_uri:
            raise RfcVoipDialogError("SIP dialog has no remote target URI.")

        body_bytes = body.encode("utf8")
        request = f"{method} {remote_uri} SIP/2.0\r\n"
        request += sip._via_header(rport=True)
        request += "Max-Forwards: 70\r\n"
        request += f"From: {local}\r\n"
        request += f"To: {remote}\r\n"
        request += f"Call-ID: {call.call_id}\r\n"
        request += f"CSeq: {cseq} {method}\r\n"
        request += sip._contact_header()
        request += f"Allow: {(', '.join(rfcvoip.SIPCompatibleMethods))}\r\n"
        request += f"User-Agent: rfcvoip {rfcvoip.__version__}\r\n"
        for header in extra_headers or []:
            request += header if header.endswith("\r\n") else header + "\r\n"
        if auth_line:
            request += auth_line
        if content_type:
            request += f"Content-Type: {content_type}\r\n"
        request += f"Content-Length: {len(body_bytes)}\r\n\r\n"
        request += body
        return request, remote_uri

    def _reinvite(self, call: Any, mode: RTP.TransmitType) -> None:
        sip = call.sip
        body = _sdp_body(call, mode)
        auth_line = ""
        retries = 0
        deadline = time.monotonic() + self.hold_timeout

        with sip.recvLock:
            while True:
                cseq = _next_dialog_cseq(sip)
                request, remote_uri = self._build_in_dialog_request(
                    call,
                    method="INVITE",
                    cseq=cseq,
                    body=body,
                    content_type="application/sdp",
                    auth_line=auth_line,
                )
                sip.send_raw(request.encode("utf8"), sip.dialog_target(call.request))

                while time.monotonic() < deadline:
                    response = sip._recv_message_before(deadline)
                    if response is None:
                        break
                    if response.type == SIP.SIPMessageType.MESSAGE:
                        sip.parse_message(response)
                        continue
                    if not _request_matches(response, str(call.call_id), cseq, "INVITE"):
                        sip.parse_message(response)
                        continue
                    code = int(response.status)
                    if 100 <= code < 200:
                        continue
                    _send_ack(sip, response, remote_uri)
                    if response.status in (
                        SIP.SIPStatus.UNAUTHORIZED,
                        SIP.SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                    ) and retries < 1:
                        auth_line = _auth_header(
                            sip,
                            response,
                            method="INVITE",
                            uri=remote_uri,
                            body=body.encode("utf8"),
                        )
                        retries += 1
                        break
                    if 200 <= code < 300:
                        _update_rtp_from_answer(call, response)
                        return
                    raise RfcVoipDialogError(f"SIP re-INVITE failed with {code} {response.status.phrase}.")
                else:
                    raise TimeoutError("SIP re-INVITE timed out.")

                if retries and auth_line:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("SIP re-INVITE timed out.")
                    continue
                raise TimeoutError("SIP re-INVITE timed out.")

    def transfer(self, call: Any, target: str) -> SipTransferResult:
        raw_target = str(target or "").strip()
        if not raw_target or any(ch in raw_target for ch in "\r\n<>"):
            raise RfcVoipDialogError("SIP transfer target is invalid.")
        target_uri = call.sip._normalize_request_target(raw_target)
        if any(ch in target_uri for ch in "\r\n<>"):
            raise RfcVoipDialogError("Normalized SIP transfer target is invalid.")
        return self._refer(call, target_uri, mode="blind", replaces_call_id=None)

    def attended_transfer(self, call: Any, consult_call: Any) -> SipTransferResult:
        if call is consult_call or str(call.call_id) == str(consult_call.call_id):
            raise RfcVoipDialogError("Attended transfer requires two different SIP calls.")
        if call.state != CallState.ANSWERED or consult_call.state != CallState.ANSWERED:
            raise RfcVoipDialogError("Attended transfer requires two answered SIP calls.")
        if call.sip is not consult_call.sip:
            raise RfcVoipDialogError("Attended transfer calls must belong to the same SIP account.")

        sip = call.sip
        _dialog_headers(sip, consult_call.request)
        target_uri = sip._dialog_remote_uri(consult_call.request)
        if not target_uri:
            raise RfcVoipDialogError("Consultation call has no SIP Contact target.")
        from_header = consult_call.request.headers.get("From", {})
        to_header = consult_call.request.headers.get("To", {})
        from_tag = str(from_header.get("tag") or "") if isinstance(from_header, dict) else ""
        to_tag = str(to_header.get("tag") or "") if isinstance(to_header, dict) else ""
        if not from_tag or not to_tag:
            raise RfcVoipDialogError("Consultation dialog tags are incomplete.")
        replaces = f"{consult_call.call_id};to-tag={to_tag};from-tag={from_tag}"
        separator = "&" if "?" in target_uri else "?"
        refer_to = f"{target_uri}{separator}Replaces={quote(replaces, safe='')}"
        if any(ch in refer_to for ch in "\r\n<>"):
            raise RfcVoipDialogError("Attended transfer target is invalid.")
        return self._refer(
            call,
            refer_to,
            mode="attended",
            replaces_call_id=str(consult_call.call_id),
        )

    def _refer(
        self,
        call: Any,
        refer_to: str,
        *,
        mode: str,
        replaces_call_id: str | None,
    ) -> SipTransferResult:
        if call.state != CallState.ANSWERED:
            raise RfcVoipDialogError("SIP transfer requires an answered call.")
        sip = call.sip
        call_id = str(call.call_id)
        local, _remote, local_tag, remote_tag = _dialog_headers(sip, call.request)
        with self._refer_lock:
            refer_ordinal = self._refer_attempts.get(call_id, 0) + 1
            self._refer_attempts[call_id] = refer_ordinal
        remote_uri = sip._dialog_remote_uri(call.request)
        if not remote_uri:
            raise RfcVoipDialogError("SIP dialog has no remote target URI.")

        subscription = SIPSubscription(
            call_id=call_id,
            target=refer_to,
            target_uri=remote_uri,
            event="refer",
            accept=["message/sipfrag"],
            local_tag=local_tag,
            remote_tag=remote_tag,
            remote_target=remote_uri,
            expires=60,
            pending_expires=60,
        )
        with sip._subscription_lock:
            previous_subscription = sip.subscriptions.get(call_id)
            if previous_subscription is not None:
                raise RfcVoipDialogError("This SIP dialog already has an active event subscription.")
            sip.subscriptions[call_id] = subscription

        try:
            accepted = self._send_refer(call, refer_to, subscription, refer_ordinal=refer_ordinal)
            if not accepted.accepted:
                return accepted
            final = self._wait_refer_notify(sip, subscription, mode, refer_to, replaces_call_id)
            return final
        finally:
            with sip._subscription_lock:
                if sip.subscriptions.get(call_id) is subscription:
                    sip.subscriptions.pop(call_id, None)

    def _send_refer(
        self,
        call: Any,
        refer_to: str,
        subscription: SIPSubscription,
        *,
        refer_ordinal: int = 1,
    ) -> SipTransferResult:
        sip = call.sip
        auth_line = ""
        retries = 0
        deadline = time.monotonic() + min(self.transfer_timeout, 15.0)
        mode = "attended" if "Replaces=" in refer_to else "blind"
        replaces_call_id = None
        if mode == "attended":
            decoded = re.search(r"Replaces=([^&>]+)", refer_to)
            if decoded:
                from urllib.parse import unquote

                replaces_call_id = unquote(decoded.group(1)).split(";", 1)[0]

        with sip.recvLock:
            while True:
                cseq = _next_dialog_cseq(sip)
                # RFC 3515 requires an Event id (the REFER CSeq) on NOTIFYs
                # for the second and subsequent REFER requests in a dialog.
                # Match that package identity before sending so an early
                # NOTIFY can be consumed even if it beats the 202 response.
                subscription.event = "refer" if refer_ordinal <= 1 else f"refer;id={cseq}"
                request, _remote_uri = self._build_in_dialog_request(
                    call,
                    method="REFER",
                    cseq=cseq,
                    extra_headers=[
                        f"Refer-To: <{refer_to}>",
                        "Supported: replaces",
                    ],
                    auth_line=auth_line,
                )
                sip.send_raw(request.encode("utf8"), sip.dialog_target(call.request))

                while time.monotonic() < deadline:
                    response = sip._recv_message_before(deadline)
                    if response is None:
                        break
                    if response.type == SIP.SIPMessageType.MESSAGE:
                        if response.method == "NOTIFY":
                            sip._handle_notify(response)
                        else:
                            sip.parse_message(response)
                        continue
                    if not _request_matches(response, str(call.call_id), cseq, "REFER"):
                        sip.parse_message(response)
                        continue
                    code = int(response.status)
                    if 100 <= code < 200:
                        continue
                    subscription.last_response_code = code
                    subscription.last_response_phrase = response.status.phrase
                    if response.status in (
                        SIP.SIPStatus.UNAUTHORIZED,
                        SIP.SIPStatus.PROXY_AUTHENTICATION_REQUIRED,
                    ) and retries < 1:
                        auth_line = _auth_header(
                            sip,
                            response,
                            method="REFER",
                            uri=sip._dialog_remote_uri(call.request),
                        )
                        retries += 1
                        break
                    if 200 <= code < 300:
                        subscription.status = "pending"
                        return SipTransferResult(
                            mode=mode,
                            target=refer_to,
                            accepted=True,
                            completed=False,
                            status_code=code,
                            phrase=response.status.phrase,
                            replaces_call_id=replaces_call_id,
                        )
                    return SipTransferResult(
                        mode=mode,
                        target=refer_to,
                        accepted=False,
                        completed=False,
                        status_code=code,
                        phrase=response.status.phrase,
                        replaces_call_id=replaces_call_id,
                    )
                else:
                    raise TimeoutError("SIP REFER transaction timed out.")

                if retries and auth_line:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("SIP REFER transaction timed out.")
                    continue
                raise TimeoutError("SIP REFER transaction timed out.")

    def _wait_refer_notify(
        self,
        sip: SIP.SIPClient,
        subscription: SIPSubscription,
        mode: str,
        target: str,
        replaces_call_id: str | None,
    ) -> SipTransferResult:
        deadline = time.monotonic() + self.transfer_timeout
        while time.monotonic() < deadline:
            code, phrase = self._sipfrag_status(subscription.last_notify_body)
            if code is not None and code >= 200:
                return SipTransferResult(
                    mode=mode,
                    target=target,
                    accepted=True,
                    completed=200 <= code < 300,
                    status_code=code,
                    phrase=phrase,
                    replaces_call_id=replaces_call_id,
                )
            with sip._subscription_lock:
                active = sip.subscriptions.get(subscription.call_id) is subscription
                state = subscription.subscription_state
                reason = subscription.reason
            if not active and state == "terminated":
                return SipTransferResult(
                    mode=mode,
                    target=target,
                    accepted=True,
                    completed=False,
                    status_code=code,
                    phrase=phrase or reason or "REFER subscription terminated without final sipfrag status",
                    replaces_call_id=replaces_call_id,
                )
            time.sleep(0.02)
        raise TimeoutError("SIP REFER did not receive a final Event: refer NOTIFY.")

    @staticmethod
    def _sipfrag_status(body: str) -> tuple[int | None, str | None]:
        match = _SIPFRAG_STATUS.search(str(body or ""))
        if not match:
            return None, None
        code = int(match.group(1))
        phrase = (match.group(2) or "").strip() or None
        return code, phrase
