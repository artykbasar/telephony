from __future__ import annotations

import base64
import binascii
import json

import frappe
from frappe import _
from frappe.utils import get_datetime

from telephony.contacts import resolve_contact_numbers

PAGE_SIZE = 20
HISTORY_FILTERS = {"all", "missed", "incoming", "outgoing"}
MISSED_STATUSES = {"No Answer", "Failed", "Busy", "Canceled"}


def _encode_cursor(start_time, name: str) -> str:
    payload = json.dumps({"start_time": get_datetime(start_time).isoformat(sep=" "), "name": str(name)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        value = str(cursor).strip()
        payload = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(payload.decode())
        return get_datetime(decoded["start_time"]), str(decoded["name"])
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        frappe.throw(_("Invalid call history cursor."), frappe.ValidationError)


def _total_duration_seconds(start_time, end_time, fallback=0) -> int:
    if start_time and end_time:
        return max(0, int((get_datetime(end_time) - get_datetime(start_time)).total_seconds()))
    return int(fallback or 0)


def _require_sip_agent(user: str) -> None:
    if user == "Guest" or not frappe.db.exists("TP Telephony Agent", {"user": user, "sip_enabled": 1}):
        frappe.throw(_("You do not have permission to view Telephony SIP call history."), frappe.PermissionError)


@frappe.whitelist()
def get_my_call_history(cursor: str | None = None, history_filter: str | None = None) -> dict:
    user = frappe.session.user
    _require_sip_agent(user)
    history_filter = str(history_filter or "all").strip().lower()
    if history_filter not in HISTORY_FILTERS:
        frappe.throw(_("Invalid call history filter."), frappe.ValidationError)

    filters = [["telephony_medium", "=", "SIP"]]
    or_filters = [["caller", "=", user], ["receiver", "=", user]]
    if history_filter == "incoming":
        filters.append(["type", "=", "Incoming"])
    elif history_filter == "outgoing":
        filters.append(["type", "=", "Outgoing"])
    elif history_filter == "missed":
        filters.extend([["type", "=", "Incoming"], ["status", "in", tuple(MISSED_STATUSES)]])

    decoded = _decode_cursor(cursor)
    if decoded:
        start_time, name = decoded
        filters.append(["start_time", "<=", start_time])

    rows = frappe.get_list(
        "TP Call Log",
        fields=["name", "id", "type", "status", "from", "to", "caller_name", "ivr_route", "caller", "receiver", "start_time", "connected_at", "end_time", "duration", "recording_url", "creation"],
        filters=filters,
        or_filters=or_filters,
        order_by="start_time desc, name desc",
        limit=PAGE_SIZE + 5,
    )
    if decoded:
        start_time, name = decoded
        rows = [row for row in rows if get_datetime(row.start_time) < start_time or (get_datetime(row.start_time) == start_time and row.name < name)]
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    remote_numbers = [
        str(row.get("from") or "") if row.type == "Incoming" else str(row.to or "")
        for row in rows
    ]
    contact_matches = resolve_contact_numbers(remote_numbers)
    items = []
    for row in rows:
        direction = row.type
        outcome = row.status
        remote_number = str(row.get("from") or "") if direction == "Incoming" else str(row.to or "")
        contact = contact_matches.get(remote_number) or {}
        items.append({
            "name": row.name,
            "call_id": row.id,
            "direction": direction,
            "outcome": outcome,
            "from_number": row.get("from"),
            "to_number": row.to,
            "caller_name": row.caller_name,
            "ivr_route": row.ivr_route,
            "contact": contact.get("contact"),
            "contact_name": contact.get("contact_name"),
            "contact_image": contact.get("image"),
            "answered_by": row.receiver if direction == "Incoming" else row.caller,
            "started_at": row.start_time,
            "connected_at": row.connected_at,
            "ended_at": row.end_time,
            "duration_seconds": row.duration or 0,
            "total_duration_seconds": _total_duration_seconds(row.start_time, row.end_time, row.duration),
            "recording": row.recording_url,
            "creation": row.creation,
        })
    next_cursor = _encode_cursor(rows[-1].start_time, rows[-1].name) if has_more and rows else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


__all__ = ["get_my_call_history"]
