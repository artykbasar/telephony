from __future__ import annotations

import re
from urllib.parse import unquote

import frappe
import phonenumbers

_SHORT_CODE_RE = re.compile(r"^[*#]?\d{1,6}#?$")
_SIP_URI_RE = re.compile(r"(?:sips?|tel):([^@;>?\s]+)", re.IGNORECASE)


def get_default_phone_region() -> str | None:
    country = str(frappe.defaults.get_global_default("country") or "").strip()
    if not country:
        return None
    code = str(frappe.db.get_value("Country", country, "code") or "").strip().upper()
    return code if len(code) == 2 else None


def _dialable_text(value: object) -> str:
    text = unquote(str(value or "").strip())
    if not text:
        return ""
    match = _SIP_URI_RE.search(text)
    if match:
        text = match.group(1)
    text = text.split(";", 1)[0].split("?", 1)[0].strip().strip("<>")
    compact = re.sub(r"[\s().\-]", "", text)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    return compact


def normalize_phone_number(value: object, default_region: str | None = None) -> str | None:
    """Return a comparison-only canonical key; callers keep the source value for display/dial."""
    compact = _dialable_text(value)
    if not compact:
        return None
    if _SHORT_CODE_RE.fullmatch(compact):
        return f"ext:{compact}"
    region = (default_region or get_default_phone_region() or "").upper() or None
    try:
        parsed = phonenumbers.parse(compact, region)
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"\D", "", compact)
    if not digits:
        return f"raw:{compact.lower()}"
    prefix = "+" if compact.startswith("+") else ""
    return f"raw:{prefix}{digits}"


__all__ = ["get_default_phone_region", "normalize_phone_number"]
