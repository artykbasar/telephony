from pathlib import Path
import frappe
from frappe import _
from frappe.utils import cint, get_bench_path

from telephony.runtime.health import read_runtime_status

from telephony.utils import link_call_with_contact, link_call_with_doc


def _get_voice_identity(user: str):
    agent_name = frappe.db.exists("TP Telephony Agent", {"user": user, "sip_enabled": 1})
    if not agent_name:
        return None
    agent = frappe.get_doc("TP Telephony Agent", agent_name)
    return {
        "user": agent.user,
        "extension": agent.sip_extension or "",
        "display_name": agent.sip_display_name or "",
    }


@frappe.whitelist()
def fetch_my_sip_config():
    """Compatibility endpoint returning only non-secret native voice capability data.

    Browser SIP credentials, PBX endpoints, STUN/TURN configuration and SIP passwords
    are intentionally never returned by the native server-side SIP architecture.
    """
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not permitted"))
    try:
        settings = frappe.get_cached_doc("TP SIP Settings")
    except frappe.DoesNotExistError:
        settings = None
    enabled = bool(settings and settings.enabled)
    identity = _get_voice_identity(user) if enabled else None
    site = getattr(frappe.local, "site", None)
    runtime_status = (
        read_runtime_status(site=site, bench_path=Path(get_bench_path()))
        if enabled and identity and site
        else {"ready": False}
    )
    return {
        "enabled": bool(enabled and identity),
        "identity": identity or {},
        "runtime_ready": bool(runtime_status.get("ready")),
        "transport": "socketio",
        "capabilities": {
            "server_side_sip": True,
            "pcm_media": True,
            "hold": True,
            "transfer": True,
            "dtmf": True,
        },
    }


def _resolve_status(status: str) -> str:
    status = (status or "").strip().lower()
    mapping = {
        "initiated": "Initiated", "dialing": "Initiated", "ringing": "Ringing",
        "progress": "In Progress", "in-progress": "In Progress", "answered": "In Progress",
        "connected": "In Progress", "completed": "Completed", "ended": "Completed",
        "hangup": "Completed", "failed": "Failed", "busy": "Busy",
        "no-answer": "No Answer", "missed": "No Answer", "queued": "Queued",
        "canceled": "Canceled", "cancelled": "Canceled",
    }
    return mapping.get(status, "Initiated")


@frappe.whitelist()
def log_sip_call(**kwargs):
    """Create or update a SIP call log from browser softphone events."""
    args = frappe._dict(kwargs)

    required = ["call_id", "direction", "from_number", "to_number"]
    for key in required:
        if not args.get(key):
            frappe.throw(_("{0} is required").format(key))

    direction = args.direction.title()
    if direction not in ("Incoming", "Outgoing"):
        frappe.throw(_("direction must be Incoming or Outgoing"))

    status = _resolve_status(args.get("status"))
    call_id = args.call_id
    dialog_id = args.get("dialog_id")

    existing = frappe.db.exists("TP Call Log", {"id": call_id})
    if existing:
        call_log = frappe.get_doc("TP Call Log", existing)
    else:
        call_log = frappe.get_doc(
            {
                "doctype": "TP Call Log",
                "id": call_id,
                "telephony_medium": "SIP",
                "type": direction,
                "from": args.from_number,
                "to": args.to_number,
            }
        )

    now = frappe.utils.now_datetime()

    # Telephony medium + human-readable medium label
    call_log.telephony_medium = "SIP"
    if not call_log.medium:
        call_log.medium = "Desk Softphone"

    call_log.status = status
    call_log.type = direction
    setattr(call_log, "from", args.from_number)
    call_log.to = args.to_number
    call_log.sip_call_id = call_id
    call_log.sip_dialog_id = dialog_id

    # Populate start_time / end_time
    if not call_log.start_time:
        call_log.start_time = now
    terminal_statuses = {"Completed", "Failed", "Busy", "No Answer", "Canceled"}
    if status in terminal_statuses:
        call_log.end_time = now

    # Caller / receiver attribution
    if direction == "Incoming":
        # For incoming calls, "receiver" is the logged-in Desk agent.
        call_log.receiver = args.get("receiver") or call_log.receiver or frappe.session.user
        # Caller is the remote party; do not override it here.
    else:
        # For outgoing calls, "caller" is the logged-in Desk agent.
        call_log.caller = args.get("caller") or call_log.caller or frappe.session.user
        # Receiver is the remote party; leave as-is.

    if args.get("duration") is not None:
        call_log.duration = cint(args.duration)

    call_log.save(ignore_permissions=True)

    # Link contacts/docs
    contact_number = args.from_number if direction == "Incoming" else args.to_number
    link_call_with_contact(contact_number, call_log)
    if args.get("link_doctype") and args.get("link_docname"):
        link_call_with_doc(call_log, args.link_doctype, args.link_docname)

    frappe.db.commit()  # nosemgrep
    return {"ok": True, "name": call_log.name, "status": call_log.status}
