from __future__ import annotations

from urllib.parse import quote

import frappe
from frappe import _
from frappe.integrations.utils import make_get_request

from telephony.voice.sip import SipIncomingCall

PROJECT_NAME = "telephony"
DEFAULT_NOTIFICATION_ICON = "/assets/telephony/softphone_media/contact_avatar.png"


def _require_agent(user: str) -> None:
    if user == "Guest" or not frappe.db.exists(
        "TP Telephony Agent", {"user": user, "sip_enabled": 1}
    ):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def get_call_notification_status() -> dict:
    """Return site/browser prerequisites without exposing relay credentials."""
    user = frappe.session.user
    _require_agent(user)
    try:
        from frappe.push_notification import PushNotification
    except ImportError:
        return {"supported": False, "relay_enabled": False, "relay_url_configured": False}

    return {
        "supported": True,
        "relay_enabled": bool(PushNotification(PROJECT_NAME).is_enabled()),
        "relay_url_configured": bool(frappe.conf.get("push_relay_server_url")),
        "project_name": PROJECT_NAME,
    }


@frappe.whitelist()
def get_call_notification_bootstrap() -> dict:
    """Fetch public Firebase bootstrap through the configured relay server-side."""
    user = frappe.session.user
    _require_agent(user)

    from frappe.push_notification import PushNotification

    if not PushNotification(PROJECT_NAME).is_enabled():
        frappe.throw(_("Push Notification Relay is not enabled"))
    relay = str(frappe.conf.get("push_relay_server_url") or "").strip().rstrip("/")
    if not relay:
        frappe.throw(_("Frappe Push Notification Relay is not configured."))

    data = make_get_request(
        f"{relay}/api/method/notification_relay.api.get_config",
        params={"project_name": PROJECT_NAME},
    )
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        data = data["message"]
    if not isinstance(data, dict):
        frappe.throw(_("The Frappe push relay returned an invalid configuration."))

    config = data.get("config")
    vapid_public_key = data.get("vapid_public_key")
    if not isinstance(config, dict) or not vapid_public_key:
        frappe.throw(_("The Frappe push relay returned an incomplete configuration."))
    return {
        "config": config,
        "vapid_public_key": str(vapid_public_key),
        "project_name": PROJECT_NAME,
    }


def _public_contact_image(value: object) -> str:
    image = str(value or "").strip()
    if not image:
        return ""
    lowered = image.casefold()
    if lowered.startswith("/private/") or lowered.startswith("private/"):
        return ""
    return image


def _resolve_contact_for_user(user: str, number: str) -> dict:
    if not number:
        return {}
    previous_user = str(getattr(getattr(frappe, "session", None), "user", "") or "Guest")
    changed_user = bool(user and user != previous_user)
    try:
        if changed_user:
            frappe.set_user(user)
        from telephony.contacts import resolve_contact_numbers

        return resolve_contact_numbers([number]).get(number) or {}
    finally:
        if changed_user:
            frappe.set_user(previous_user)


def _call_notification_title(call: SipIncomingCall, contact: dict | None = None) -> str:
    contact = contact or {}
    return str(
        contact.get("contact_name")
        or call.caller_name
        or call.caller_id
        or _("Unknown caller")
    ).strip()


def _call_notification_body(call: SipIncomingCall, contact: dict | None = None) -> str:
    contact = contact or {}
    title = _call_notification_title(call, contact)
    parts = [_('Incoming call')]
    number = str(call.caller_id or "").strip()
    if number and number != title:
        parts.append(number)
    company = str(contact.get("company_name") or "").strip()
    if company and company not in parts:
        parts.append(company)
    route = str(call.ivr_route or "").strip()
    if route and route not in parts:
        parts.append(route)
    return " · ".join(part for part in parts if part)


def send_incoming_call_notification(user: str, call: SipIncomingCall) -> bool:
    """Best-effort push; notification failure must never affect SIP call control."""
    try:
        from frappe.push_notification import PushNotification

        push = PushNotification(PROJECT_NAME)
        if not push.is_enabled():
            return False
        call_id = str(call.call_id or "").strip()
        caller_number = str(call.caller_id or "").strip()
        contact = _resolve_contact_for_user(user, caller_number)
        contact_image = _public_contact_image(contact.get("image"))
        notification_icon = contact_image or DEFAULT_NOTIFICATION_ICON
        contact_name = str(contact.get("contact_name") or "").strip()
        notification_title = _call_notification_title(call, contact)
        notification_body = _call_notification_body(call, contact)
        link = f"{frappe.utils.get_url()}/app?telephony_call={quote(call_id, safe='')}"
        data = {
            "_frappe_push_data_only": "1",
            "type": "telephony_incoming_call",
            "call_id": call_id,
            "title": notification_title,
            "body": notification_body,
            "caller_number": caller_number,
            "caller_name": contact_name or str(call.caller_name or ""),
            "sip_caller_name": str(call.caller_name or ""),
            "ivr_route": str(call.ivr_route or ""),
            "contact": str(contact.get("contact") or ""),
            "contact_name": contact_name,
            "company_name": str(contact.get("company_name") or ""),
            "designation": str(contact.get("designation") or ""),
            "department": str(contact.get("department") or ""),
            "contact_image": contact_image,
        }
        if contact_image:
            data["notification_image"] = contact_image
        return bool(push.send_notification_to_user(
            user,
            notification_title,
            notification_body,
            link=link,
            icon=notification_icon,
            data=data,
        ))
    except Exception:
        frappe.log_error(title="Telephony incoming call push failed")
        return False


__all__ = [
    "PROJECT_NAME",
    "get_call_notification_bootstrap",
    "get_call_notification_status",
    "send_incoming_call_notification",
]
