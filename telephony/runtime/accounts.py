from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import frappe

from telephony.voice.sip import SipAccountConfig, TelephonyAudioFormat


@dataclass(frozen=True, slots=True, repr=False)
class TelephonyRuntimeAccount:
    agent: str
    user: str
    extension: str | None
    display_name: str | None
    config: SipAccountConfig


def build_runtime_account(settings, agent) -> TelephonyRuntimeAccount:
    password = agent.get_password("sip_password", raise_exception=False) or ""
    override = bool(getattr(agent, "override_sip_settings", False))
    if override:
        server = (getattr(agent, "sip_override_server", None) or "").strip()
        port = int(getattr(agent, "sip_override_port", None) or 5060)
        transport = (getattr(agent, "sip_override_transport", None) or "UDP").upper()
        local_ip = (getattr(agent, "sip_override_local_bind_address", None) or "0.0.0.0").strip()
        advertised_ip = getattr(agent, "sip_override_advertised_address", None) or None
        tls_server_name = getattr(agent, "sip_override_tls_server_name", None) or None
        proxy = getattr(agent, "sip_override_proxy", None) or None
        proxy_port_value = getattr(agent, "sip_override_proxy_port", None)
    else:
        server = (settings.sip_server or "").strip()
        port = int(settings.sip_port or 5060)
        transport = (settings.transport or "UDP").upper()
        local_ip = (settings.local_bind_address or "0.0.0.0").strip()
        advertised_ip = settings.advertised_address or None
        tls_server_name = settings.tls_server_name or None
        proxy = settings.proxy or None
        proxy_port_value = settings.proxy_port

    config = SipAccountConfig(
        server=server,
        port=port,
        username=(agent.sip_username or "").strip(),
        password=password,
        proxy=proxy,
        proxy_port=int(proxy_port_value) if proxy_port_value else None,
        transport=transport,
        local_ip=local_ip,
        advertised_ip=advertised_ip,
        tls_server_name=tls_server_name,
        audio_format=TelephonyAudioFormat(),
    )
    return TelephonyRuntimeAccount(
        agent=agent.name,
        user=agent.user,
        extension=(agent.sip_extension or None),
        display_name=(agent.sip_display_name or None),
        config=config,
    )


def load_enabled_sip_accounts(*, site: str, bench_path: Path) -> tuple[TelephonyRuntimeAccount, ...]:
    sites_path = Path(bench_path).resolve() / "sites"
    previous_cwd = Path.cwd()
    os.chdir(sites_path)
    try:
        frappe.init(site=site, sites_path=".")
        frappe.connect()
        settings = frappe.get_single("TP SIP Settings")
        if not settings.enabled:
            return ()
        names = frappe.get_all(
            "TP Telephony Agent",
            filters={"sip_enabled": 1},
            pluck="name",
            order_by="creation asc, name asc",
        )
        return tuple(build_runtime_account(settings, frappe.get_doc("TP Telephony Agent", name)) for name in names)
    finally:
        frappe.destroy()
        os.chdir(previous_cwd)


__all__ = ["TelephonyRuntimeAccount", "build_runtime_account", "load_enabled_sip_accounts"]
