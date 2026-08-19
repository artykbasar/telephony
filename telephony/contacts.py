from __future__ import annotations

import json

import frappe
from frappe import _

from telephony.phone_numbers import get_default_phone_region, normalize_phone_number

CONTACT_PAGE_SIZE = 20
CONTACT_SCAN_CHUNK = 100
RESOLVE_LIMIT = 100


def _require_sip_agent(user: str) -> None:
    if user == "Guest" or not frappe.db.exists("TP Telephony Agent", {"user": user, "sip_enabled": 1}):
        frappe.throw(_("You do not have permission to view Telephony contacts."), frappe.PermissionError)


def _contact_display_name(item: dict) -> str:
    full_name = str(item.get("full_name") or "").strip()
    if full_name:
        return full_name
    combined = " ".join(str(item.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    return combined or str(item.get("company_name") or item.get("name") or _("Contact")).strip()


def _coerce_numbers(numbers) -> list[str]:
    if isinstance(numbers, str):
        try:
            decoded = json.loads(numbers)
            numbers = decoded if isinstance(decoded, list) else [numbers]
        except (TypeError, ValueError, json.JSONDecodeError):
            numbers = [numbers]
    if not isinstance(numbers, (list, tuple)):
        numbers = [numbers]
    values = []
    seen = set()
    for value in numbers:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)
    if len(values) > RESOLVE_LIMIT:
        frappe.throw(_("Too many phone numbers to resolve."), frappe.ValidationError)
    return values


def resolve_contact_numbers(numbers, *, default_region: str | None = None) -> dict[str, dict | None]:
    values = _coerce_numbers(numbers)
    if not values:
        return {}
    region = default_region or get_default_phone_region()
    requested = {value: normalize_phone_number(value, region) for value in values}
    wanted = {key for key in requested.values() if key}
    if not wanted:
        return {value: None for value in values}

    rows = frappe.get_list(
        "Contact",
        fields=[
            "name", "full_name", "first_name", "last_name", "mobile_no", "phone",
            "company_name", "department", "designation", "image",
        ],
        order_by="full_name asc, name asc",
        limit_page_length=10000,
    )
    contacts = {str(row.name): dict(row) for row in rows}
    owners: dict[str, set[str]] = {}
    matched_source: dict[tuple[str, str], str] = {}

    def add(contact: str, number: object) -> None:
        key = normalize_phone_number(number, region)
        if not key or key not in wanted or contact not in contacts:
            return
        owners.setdefault(key, set()).add(contact)
        matched_source.setdefault((key, contact), str(number or "").strip())

    for contact, item in contacts.items():
        add(contact, item.get("mobile_no"))
        add(contact, item.get("phone"))
    if contacts:
        for row in frappe.get_all(
            "Contact Phone",
            filters={"parenttype": "Contact"},
            fields=["parent", "phone"],
            limit_page_length=50000,
        ):
            add(str(row.parent), row.phone)

    unique: dict[str, dict] = {}
    for key, contact_names in owners.items():
        if len(contact_names) != 1:
            continue
        contact = next(iter(contact_names))
        item = contacts[contact]
        unique[key] = {
            "contact": contact,
            "contact_name": _contact_display_name(item),
            "company_name": item.get("company_name"),
            "department": item.get("department"),
            "designation": item.get("designation"),
            "image": item.get("image"),
            "matched_number": matched_source.get((key, contact)),
            "canonical": key,
        }
    return {value: unique.get(requested[value]) for value in values}


@frappe.whitelist()
def resolve_numbers(numbers) -> dict:
    _require_sip_agent(frappe.session.user)
    return {"matches": resolve_contact_numbers(numbers)}


SEARCH_PARENT_FIELDS = (
    "name",
    "full_name",
    "first_name",
    "middle_name",
    "last_name",
    "email_id",
    "user",
    "status",
    "salutation",
    "gender",
    "phone",
    "mobile_no",
    "department",
    "designation",
    "address",
    "google_contacts",
    "google_contacts_id",
    "company_name",
)
CONTACT_VIEW_FIELDS = [
    "name",
    "full_name",
    "first_name",
    "middle_name",
    "last_name",
    "email_id",
    "user",
    "status",
    "salutation",
    "gender",
    "phone",
    "mobile_no",
    "department",
    "designation",
    "address",
    "google_contacts",
    "google_contacts_id",
    "company_name",
    "image",
]
SEARCH_CANDIDATE_LIMIT = 1500
CHILD_VALUE_LIMIT = 10000


def _query_tokens(query: str) -> list[str]:
    return [part.casefold() for part in query.split() if part][:8]


def _phone_digits(value: object) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _phone_like_pattern(token: str) -> str | None:
    digits = _phone_digits(token)
    compact = "".join(character for character in token if not character.isspace())
    if len(digits) < 4 or any(character not in "+-().0123456789" for character in compact):
        return None
    return "%" + "%".join(digits) + "%"


def _row_name(row: object) -> str:
    if isinstance(row, dict):
        return str(row.get("name") or "")
    return str(getattr(row, "name", "") or "")


def _row_parent(row: object) -> str:
    if isinstance(row, dict):
        return str(row.get("parent") or "")
    return str(getattr(row, "parent", "") or "")


def _candidate_names_for_token(token: str) -> set[str]:
    pattern = f"%{token}%"
    parent_filters = [[field, "like", pattern] for field in SEARCH_PARENT_FIELDS]
    phone_pattern = _phone_like_pattern(token)
    if phone_pattern:
        parent_filters.extend([["mobile_no", "like", phone_pattern], ["phone", "like", phone_pattern]])
    names = {
        _row_name(row)
        for row in frappe.get_list(
            "Contact",
            fields=["name"],
            or_filters=parent_filters,
            limit_page_length=SEARCH_CANDIDATE_LIMIT,
        )
        if _row_name(row)
    }

    phone_filters = [["phone", "like", pattern]]
    if phone_pattern:
        phone_filters.append(["phone", "like", phone_pattern])
    for row in frappe.get_all(
        "Contact Phone",
        filters={"parenttype": "Contact"},
        or_filters=phone_filters,
        fields=["parent"],
        limit_page_length=SEARCH_CANDIDATE_LIMIT,
    ):
        if parent := _row_parent(row):
            names.add(parent)

    for row in frappe.get_all(
        "Contact Email",
        filters={"parenttype": "Contact"},
        or_filters=[["email_id", "like", pattern]],
        fields=["parent"],
        limit_page_length=SEARCH_CANDIDATE_LIMIT,
    ):
        if parent := _row_parent(row):
            names.add(parent)

    for row in frappe.get_all(
        "Dynamic Link",
        filters={"parenttype": "Contact", "parentfield": "links"},
        or_filters=[
            ["link_doctype", "like", pattern],
            ["link_name", "like", pattern],
            ["link_title", "like", pattern],
        ],
        fields=["parent"],
        limit_page_length=SEARCH_CANDIDATE_LIMIT,
    ):
        if parent := _row_parent(row):
            names.add(parent)
    return names


def _load_child_values(contact_names: list[str]) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[dict]]]:
    phones: dict[str, list[str]] = {name: [] for name in contact_names}
    emails: dict[str, list[str]] = {name: [] for name in contact_names}
    links: dict[str, list[dict]] = {name: [] for name in contact_names}
    if not contact_names:
        return phones, emails, links
    filters = {"parenttype": "Contact", "parent": ["in", contact_names]}
    for row in frappe.get_all("Contact Phone", filters=filters, fields=["parent", "phone"], limit_page_length=CHILD_VALUE_LIMIT):
        parent = _row_parent(row)
        value = str((row.get("phone") if isinstance(row, dict) else getattr(row, "phone", "")) or "").strip()
        if parent in phones and value and value not in phones[parent]:
            phones[parent].append(value)
    for row in frappe.get_all("Contact Email", filters=filters, fields=["parent", "email_id"], limit_page_length=CHILD_VALUE_LIMIT):
        parent = _row_parent(row)
        value = str((row.get("email_id") if isinstance(row, dict) else getattr(row, "email_id", "")) or "").strip()
        if parent in emails and value and value not in emails[parent]:
            emails[parent].append(value)
    link_filters = {"parenttype": "Contact", "parentfield": "links", "parent": ["in", contact_names]}
    for row in frappe.get_all(
        "Dynamic Link",
        filters=link_filters,
        fields=["parent", "link_doctype", "link_name", "link_title"],
        limit_page_length=CHILD_VALUE_LIMIT,
    ):
        data = dict(row)
        parent = str(data.pop("parent", "") or "")
        if parent in links:
            links[parent].append(data)
    return phones, emails, links


def _search_values(item: dict, phones: list[str], emails: list[str], links: list[dict]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    labels = {
        "email_id": "Email",
        "user": "User",
        "status": "Status",
        "salutation": "Salutation",
        "gender": "Gender",
        "department": "Department",
        "designation": "Designation",
        "address": "Address",
        "google_contacts": "Google Contacts",
        "google_contacts_id": "Google Contact ID",
        "company_name": "Company",
    }
    for field in SEARCH_PARENT_FIELDS:
        value = str(item.get(field) or "").strip()
        if value:
            values.append((labels.get(field, "Name" if "name" in field else "Phone"), value))
    values.extend(("Phone", value) for value in phones if value)
    values.extend(("Email", value) for value in emails if value)
    for link in links:
        title = str(link.get("link_title") or link.get("link_name") or "").strip()
        doctype = str(link.get("link_doctype") or "").strip()
        if title:
            values.append((doctype or "Linked record", title))
        if doctype:
            values.append(("Linked record", doctype))
    return values


def _token_matches(token: str, label: str, value: str) -> bool:
    folded = value.casefold()
    if token in folded:
        return True
    if label == "Phone":
        digits = _phone_digits(token)
        return len(digits) >= 4 and digits in _phone_digits(value)
    return False


def _preferred_number(item: dict, phones: list[str], tokens: list[str]) -> str:
    numbers: list[str] = []
    for value in (item.get("mobile_no"), item.get("phone"), *phones):
        number = str(value or "").strip()
        if number and number not in numbers:
            numbers.append(number)
    phone_tokens = [token for token in tokens if len(_phone_digits(token)) >= 4]
    for token in phone_tokens:
        digits = _phone_digits(token)
        for number in numbers:
            if digits in _phone_digits(number):
                return number
    return numbers[0] if numbers else ""


def _search_rank(item: dict, values: list[tuple[str, str]], tokens: list[str], query: str) -> int:
    display = _contact_display_name(item).casefold()
    folded_query = query.casefold()
    score = 0
    if display == folded_query:
        score += 200
    elif display.startswith(folded_query):
        score += 120
    for token in tokens:
        if display.startswith(token):
            score += 35
        for label, value in values:
            folded = value.casefold()
            if folded == token:
                score += 25
                break
            if folded.startswith(token):
                score += 15
                break
            if _token_matches(token, label, value):
                score += 8
                break
    return score


def _match_hint(values: list[tuple[str, str]], tokens: list[str], number: str, company_name: str | None) -> dict | None:
    ignored = {str(number or "").casefold(), str(company_name or "").casefold()}
    preferred_labels = ("Email", "Department", "Designation", "Address", "User", "Phone")
    for preferred in preferred_labels:
        for label, value in values:
            if label != preferred or value.casefold() in ignored:
                continue
            if any(_token_matches(token, label, value) for token in tokens):
                return {"label": label, "value": value}
    for label, value in values:
        if value.casefold() in ignored or label == "Name":
            continue
        if any(_token_matches(token, label, value) for token in tokens):
            return {"label": label, "value": value}
    return None


def _contact_cursor_offset(cursor: str | None, mode: str) -> int:
    raw = str(cursor or "").strip()
    prefix = f"{mode}:"
    if not raw.startswith(prefix):
        return 0
    try:
        return max(0, int(raw[len(prefix):]))
    except (TypeError, ValueError):
        return 0


def _enrich_contact_rows(rows, tokens: list[str], query: str) -> list[tuple[int, int, str, dict]]:
    raw_items = [dict(row) for row in rows]
    contact_names = [str(item.get("name") or "") for item in raw_items if item.get("name")]
    phones, emails, links = _load_child_values(contact_names)
    enriched: list[tuple[int, int, str, dict]] = []
    for index, item in enumerate(raw_items):
        contact_name = str(item.get("name") or "")
        child_phones = phones.get(contact_name, [])
        child_emails = emails.get(contact_name, [])
        child_links = links.get(contact_name, [])
        values = _search_values(item, child_phones, child_emails, child_links)
        if tokens and not all(any(_token_matches(token, label, value) for label, value in values) for token in tokens):
            continue
        number = _preferred_number(item, child_phones, tokens)
        if not number:
            continue
        item["number"] = number
        item["additional_phones"] = child_phones
        item["additional_emails"] = child_emails
        item["links"] = child_links
        if tokens:
            item["match_hint"] = _match_hint(values, tokens, number, item.get("company_name"))
        score = _search_rank(item, values, tokens, query) if tokens else 0
        enriched.append((index, score, _contact_display_name(item).casefold(), item))
    return enriched


@frappe.whitelist()
def create_contact_from_call(
    first_name: str,
    phone: str,
    last_name: str | None = None,
    email: str | None = None,
    company_name: str | None = None,
    department: str | None = None,
    designation: str | None = None,
) -> dict:
    _require_sip_agent(frappe.session.user)
    first = str(first_name or "").strip()
    number = str(phone or "").strip()
    if not first:
        frappe.throw(_("First name is required."), frappe.ValidationError)
    if not number:
        frappe.throw(_("Phone number is required."), frappe.ValidationError)

    canonical = normalize_phone_number(number, get_default_phone_region())
    if not canonical:
        frappe.throw(_("Enter a valid phone number."), frappe.ValidationError)

    contact = frappe.get_doc({
        "doctype": "Contact",
        "first_name": first,
        "last_name": str(last_name or "").strip() or None,
        "company_name": str(company_name or "").strip() or None,
        "department": str(department or "").strip() or None,
        "designation": str(designation or "").strip() or None,
    })
    contact.append("phone_nos", {"phone": number, "is_primary_mobile_no": 1})
    email_value = str(email or "").strip()
    if email_value:
        contact.append("email_ids", {"email_id": email_value, "is_primary": 1})
    contact.insert()
    return {"item": get_contact_details(contact.name)["item"]}


@frappe.whitelist()
def add_number_to_contact(contact_name: str, phone: str) -> dict:
    _require_sip_agent(frappe.session.user)
    name = str(contact_name or "").strip()
    number = str(phone or "").strip()
    if not name or not number:
        frappe.throw(_("Contact and phone number are required."), frappe.ValidationError)

    region = get_default_phone_region()
    canonical = normalize_phone_number(number, region)
    if not canonical:
        frappe.throw(_("Enter a valid phone number."), frappe.ValidationError)

    contact = frappe.get_doc("Contact", name)
    contact.check_permission("write")
    existing_numbers = [contact.mobile_no, contact.phone, *[row.phone for row in (contact.phone_nos or [])]]
    already_present = any(
        normalize_phone_number(value, region) == canonical
        for value in existing_numbers
        if str(value or "").strip()
    )
    if not already_present:
        contact.append("phone_nos", {"phone": number})
        contact.save()

    return {"item": get_contact_details(name)["item"], "added": not already_present}


@frappe.whitelist()
def get_contact_details(contact_name: str) -> dict:
    _require_sip_agent(frappe.session.user)
    name = str(contact_name or "").strip()
    if not name:
        return {"item": None}
    rows = frappe.get_list(
        "Contact",
        filters={"name": name},
        fields=CONTACT_VIEW_FIELDS,
        limit=1,
    )
    if not rows:
        return {"item": None}
    item = dict(rows[0])
    resolved_name = str(item.get("name") or "")
    phones, emails, links = _load_child_values([resolved_name])
    child_phones = phones.get(resolved_name, [])
    item["number"] = _preferred_number(item, child_phones, [])
    item["additional_phones"] = child_phones
    item["additional_emails"] = emails.get(resolved_name, [])
    item["links"] = links.get(resolved_name, [])
    return {"item": item}


@frappe.whitelist()
def get_contacts(search: str | None = None, cursor: str | None = None) -> dict:
    _require_sip_agent(frappe.session.user)
    query = " ".join(str(search or "").strip().split())[:160]
    tokens = _query_tokens(query)

    if tokens:
        candidate_names: set[str] | None = None
        for token in tokens:
            matches = _candidate_names_for_token(token)
            candidate_names = matches if candidate_names is None else candidate_names & matches
            if not candidate_names:
                return {"items": [], "query": query, "has_more": False, "next_cursor": None}
        rows = frappe.get_list(
            "Contact",
            filters={"name": ["in", sorted(candidate_names)]},
            fields=CONTACT_VIEW_FIELDS,
            order_by="full_name asc, name asc",
            limit_page_length=SEARCH_CANDIDATE_LIMIT,
        )
        ranked = _enrich_contact_rows(rows, tokens, query)
        ranked.sort(key=lambda entry: (-entry[1], entry[2], str(entry[3].get("name") or "")))
        offset = _contact_cursor_offset(cursor, "s")
        page = ranked[offset:offset + CONTACT_PAGE_SIZE]
        next_offset = offset + len(page)
        has_more = next_offset < len(ranked)
        return {
            "items": [entry[3] for entry in page],
            "query": query,
            "has_more": has_more,
            "next_cursor": f"s:{next_offset}" if has_more else None,
        }

    scan_offset = _contact_cursor_offset(cursor, "n")
    page_items: list[dict] = []
    page_end_offset = scan_offset
    has_more = False

    while len(page_items) < CONTACT_PAGE_SIZE:
        rows = frappe.get_list(
            "Contact",
            fields=CONTACT_VIEW_FIELDS,
            order_by="full_name asc, name asc",
            limit_start=scan_offset,
            limit_page_length=CONTACT_SCAN_CHUNK,
        )
        if not rows:
            break
        raw_count = len(rows)
        enriched = _enrich_contact_rows(rows, [], query)
        for row_index, _score, _display, item in enriched:
            page_items.append(item)
            page_end_offset = scan_offset + row_index + 1
            if len(page_items) >= CONTACT_PAGE_SIZE:
                has_more = row_index < raw_count - 1 or raw_count >= CONTACT_SCAN_CHUNK
                break
        if len(page_items) >= CONTACT_PAGE_SIZE:
            break
        scan_offset += raw_count
        page_end_offset = scan_offset
        if raw_count < CONTACT_SCAN_CHUNK:
            break

    return {
        "items": page_items,
        "query": query,
        "has_more": has_more,
        "next_cursor": f"n:{page_end_offset}" if has_more else None,
    }


__all__ = ["add_number_to_contact", "create_contact_from_call", "get_contact_details", "get_contacts", "resolve_contact_numbers", "resolve_numbers"]
