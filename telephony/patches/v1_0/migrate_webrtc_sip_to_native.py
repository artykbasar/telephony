from __future__ import annotations

from urllib.parse import urlparse

import frappe


def legacy_sip_host(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    return str(parsed.hostname or "").strip()


def _set_single(fieldname: str, value: object) -> None:
    frappe.db.set_single_value("TP SIP Settings", fieldname, value)


def execute() -> None:
    settings = frappe.get_single("TP SIP Settings")
    if not settings.enabled or str(settings.sip_server or "").strip():
        return

    agents = frappe.get_all(
        "TP Telephony Agent",
        filters={"sip_enabled": 1},
        fields=["name", "sip_server", "override_sip_settings", "sip_override_server"],
    )
    global_host = legacy_sip_host(getattr(settings, "wss_uri", None))
    if not global_host:
        agent_hosts = {legacy_sip_host(row.sip_server) for row in agents}
        agent_hosts.discard("")
        if len(agent_hosts) == 1:
            global_host = next(iter(agent_hosts))

    if not global_host:
        _set_single("enabled", 0)
        frappe.db.commit()
        print(
            "TELEPHONY_MIGRATION_NATIVE_SIP_DISABLED "
            "reason=no_legacy_pbx_host; configure TP SIP Settings and re-enable SIP",
            flush=True,
        )
        return

    sip_port = int(getattr(settings, "sip_port", None) or 5060)
    transport = str(getattr(settings, "transport", None) or "UDP").upper()
    local_bind_address = str(getattr(settings, "local_bind_address", None) or "0.0.0.0")
    _set_single("sip_server", global_host)
    _set_single("sip_port", sip_port)
    _set_single("transport", transport)
    _set_single("local_bind_address", local_bind_address)

    for row in agents:
        legacy_host = legacy_sip_host(row.sip_server)
        if not legacy_host or legacy_host == global_host or row.override_sip_settings or row.sip_override_server:
            continue
        frappe.db.set_value(
            "TP Telephony Agent",
            row.name,
            {
                "override_sip_settings": 1,
                "sip_override_server": legacy_host,
                "sip_override_port": 5060,
                "sip_override_transport": "UDP",
                "sip_override_local_bind_address": "0.0.0.0",
            },
            update_modified=False,
        )

    frappe.db.commit()
    print(
        f"TELEPHONY_MIGRATION_NATIVE_SIP_READY server={global_host} port={sip_port} transport={transport}",
        flush=True,
    )
