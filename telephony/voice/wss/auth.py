from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

import frappe

TICKET_VERSION = 1
TICKET_TTL_SECONDS = 30
VOICE_PROTOCOL_VERSION = 2


class VoiceTicketError(ValueError):
    pass


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise VoiceTicketError("Invalid Telephony voice ticket encoding.") from exc


def _ticket_secret(*, site: str, bench_path: Path) -> bytes:
    config_path = (Path(bench_path).resolve() / "sites" / site / "site_config.json").resolve()
    sites_path = (Path(bench_path).resolve() / "sites").resolve()
    if sites_path not in config_path.parents:
        raise VoiceTicketError("Invalid Telephony site in voice ticket.")
    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceTicketError("Telephony site configuration is unavailable.") from exc
    secret = str(config.get("encryption_key") or "").strip()
    if not secret:
        raise VoiceTicketError("Telephony voice WSS requires a site encryption key.")
    return hashlib.sha256(("frappe-telephony-wss:" + secret).encode()).digest()


def _payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def create_ticket(*, site: str, user: str, bench_path: Path, ttl_seconds: int = TICKET_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {
        "v": TICKET_VERSION,
        "site": site,
        "user": user,
        "iat": now,
        "exp": now + max(5, min(int(ttl_seconds), 120)),
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = _b64encode(_payload_bytes(payload))
    signature = hmac.new(_ticket_secret(site=site, bench_path=bench_path), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_ticket(ticket: str, *, bench_path: Path, expected_site: str | None = None, now: int | None = None) -> dict[str, Any]:
    try:
        encoded, encoded_signature = ticket.split(".", 1)
        payload = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VoiceTicketError("Invalid Telephony voice ticket.") from exc
    if not isinstance(payload, dict):
        raise VoiceTicketError("Invalid Telephony voice ticket payload.")
    site = str(payload.get("site") or "").strip()
    user = str(payload.get("user") or "").strip()
    if not site or not user or payload.get("v") != TICKET_VERSION:
        raise VoiceTicketError("Invalid Telephony voice ticket claims.")
    if expected_site and site != expected_site:
        raise VoiceTicketError("Telephony voice ticket belongs to another site.")
    expected_signature = hmac.new(
        _ticket_secret(site=site, bench_path=bench_path), encoded.encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_signature, _b64decode(encoded_signature)):
        raise VoiceTicketError("Invalid Telephony voice ticket signature.")
    current = int(time.time()) if now is None else int(now)
    if int(payload.get("iat") or 0) > current + 5 or int(payload.get("exp") or 0) < current:
        raise VoiceTicketError("Telephony voice ticket has expired.")
    return payload


@frappe.whitelist()
def issue_ticket() -> dict[str, Any]:
    from frappe.utils import get_bench_path

    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("You must be signed in to use Telephony voice.", frappe.PermissionError)
    settings = frappe.get_cached_doc("TP SIP Settings")
    agent = frappe.db.exists("TP Telephony Agent", {"user": user, "sip_enabled": 1})
    if not settings.enabled or not agent:
        frappe.throw("You do not have an enabled Telephony SIP agent.", frappe.PermissionError)
    site = frappe.local.site
    return {
        "ticket": create_ticket(site=site, user=user, bench_path=Path(get_bench_path())),
        "protocol_version": VOICE_PROTOCOL_VERSION,
        "expires_in": TICKET_TTL_SECONDS,
    }


__all__ = ["VOICE_PROTOCOL_VERSION", "VoiceTicketError", "create_ticket", "issue_ticket", "verify_ticket"]
