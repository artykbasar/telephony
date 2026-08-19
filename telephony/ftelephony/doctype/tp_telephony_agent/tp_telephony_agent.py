import ipaddress

import frappe
from frappe import _
from frappe.model.document import Document


class TPTelephonyAgent(Document):
    def validate(self):
        if not self.sip_enabled:
            return
        if not (self.sip_username or "").strip():
            frappe.throw(_("SIP Username is required when SIP is enabled"))
        if not self.sip_password:
            frappe.throw(_("SIP Password is required when SIP is enabled"))
        if not getattr(self, "override_sip_settings", 0):
            return
        if not (self.sip_override_server or "").strip():
            frappe.throw(_("SIP Server is required when agent SIP settings override is enabled"))
        port = int(self.sip_override_port or 5060)
        if not 1 <= port <= 65535:
            frappe.throw(_("Agent SIP Port must be between 1 and 65535"))
        transport = (self.sip_override_transport or "UDP").upper()
        if transport not in {"UDP", "TCP", "TLS"}:
            frappe.throw(_("Agent SIP Transport must be UDP, TCP, or TLS"))
        bind = (self.sip_override_local_bind_address or "0.0.0.0").strip()
        try:
            ipaddress.ip_address(bind)
        except ValueError:
            frappe.throw(_("Agent Local Bind Address must be an IP address"))
        if self.sip_override_proxy_port and not 1 <= int(self.sip_override_proxy_port) <= 65535:
            frappe.throw(_("Agent Proxy Port must be between 1 and 65535"))
