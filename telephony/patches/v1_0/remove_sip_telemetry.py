from __future__ import annotations

import frappe


DOCTYPE = "TP SIP Telemetry Event"


def execute() -> None:
    if frappe.db.exists("DocType", DOCTYPE):
        frappe.delete_doc(
            "DocType",
            DOCTYPE,
            force=True,
            ignore_missing=True,
            ignore_permissions=True,
        )
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{DOCTYPE}`")
    frappe.clear_cache(doctype=DOCTYPE)
