from email.parser import Parser
from email.policy import SMTP
from email.utils import parseaddr
import inspect

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import split_emails, validate_email_address

_SENDMAIL_SUPPORTS_REDACTION = "redact_message_after_send" in inspect.signature(frappe.sendmail).parameters
_REDACTED_MESSAGE = "[THE FOLLOWING CONTENT HAS BEEN REDACTED FOR SECURITY REASONS]"


from telephony.otp import (
    GENERATE_LIMIT,
    RATE_LIMIT_FIELD,
    RATE_LIMIT_WINDOW,
    VERIFY_LIMIT,
    clean_purpose,
    create_otp_record,
    generate_otp_code,
    rate_limit_bucket,
    verify_otp_record,
)


def clean_email(email: str) -> str:
    """Canonicalize for rate-limit bucketing. Must agree with what
    ``validate_single_email`` stores, or one inbox gets a quota per spelling,
    and must never throw — it yields ``""`` on unparseable input."""
    email = (email or "").replace("\n", ",").replace("\r", ",").strip().lower()
    return parseaddr(email)[1]


def validate_single_email(email: str) -> str:
    """Require exactly one address: ``validate_email_address`` accepts a list
    and returns it re-joined, which would store two recipients as one. Count
    what it *returns*, not the input — the two disagree on newlines."""
    email = clean_email(email)
    if not email:
        frappe.throw(_("Please provide a valid email address."))

    validated = validate_email_address(email, throw=True)
    if len(split_emails(validated.replace("\n", ",").replace("\r", ","))) != 1:
        frappe.throw(_("Please provide a single email address."))

    return validated


def get_email_otp_settings():
    settings = frappe.get_cached_doc("TP OTP Settings")
    if not settings.enable_email_otp:
        frappe.throw(_("Please enable Email OTP in TP OTP Settings."))
    return settings


def _redact_sent_queue_message(queue_name):
    status, message = frappe.db.get_value(
        "Email Queue", queue_name, ["status", "message"]
    ) or (None, None)
    if status != "Sent" or not message:
        return

    parsed = Parser(policy=SMTP).parsestr(message)
    parsed.clear_content()
    parsed.set_content(_REDACTED_MESSAGE)
    frappe.db.set_value(
        "Email Queue", queue_name, "message", parsed.as_string(), update_modified=False
    )
    frappe.db.commit()


def dispatch_email_otp(email, message, subject):
    kwargs = {"recipients": [email], "subject": subject, "message": message}
    if _SENDMAIL_SUPPORTS_REDACTION:
        frappe.sendmail(**kwargs, redact_message_after_send=True)
        return

    # Older supported Frappe versions have no redaction flag. Send immediately
    # after commit, then redact only after a successful send so retries retain
    # the original body if delivery fails.
    queue = frappe.sendmail(**kwargs, now=True)
    if queue and getattr(queue, "name", None):
        frappe.db.after_commit.add(
            lambda queue_name=queue.name: _redact_sent_queue_message(queue_name)
        )


# ip_based=False: the default mixes in the client IP, so rotating IPs would
# buy unlimited mail to a single inbox.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
@rate_limit_bucket(
    "email", clean_email, "email:generate", GENERATE_LIMIT, RATE_LIMIT_WINDOW
)
@rate_limit(
    key=RATE_LIMIT_FIELD,
    limit=GENERATE_LIMIT,
    seconds=RATE_LIMIT_WINDOW,
    ip_based=False,
)
def generate_otp(email: str, purpose: str = "Verification"):
    """Generate an OTP and send it over email to the given address."""
    settings = get_email_otp_settings()
    email = validate_single_email(email)

    purpose = clean_purpose(purpose)
    otp = generate_otp_code(settings.otp_length)
    expiry_seconds = settings.otp_expiry_in_seconds

    message = settings.otp_message_template.format(
        otp=otp, expiry_minutes=max(1, expiry_seconds // 60)
    )
    subject = settings.email_otp_subject

    # Record first: the mail is queued in the same transaction, so a failure
    # rolls both back together.
    create_otp_record(
        email, "Email", purpose, otp, expiry_seconds, settings.otp_max_attempts
    )
    dispatch_email_otp(email, message, subject)

    return {"sent": True, "expires_in": expiry_seconds}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
@rate_limit_bucket(
    "email", clean_email, "email:verify", VERIFY_LIMIT, RATE_LIMIT_WINDOW
)
@rate_limit(
    key=RATE_LIMIT_FIELD, limit=VERIFY_LIMIT, seconds=RATE_LIMIT_WINDOW, ip_based=False
)
def verify_otp(email: str, otp: str, purpose: str = "Verification"):
    """Verify an OTP previously sent to the given email address."""
    settings = get_email_otp_settings()
    email = validate_single_email(email)

    return verify_otp_record(
        email, "Email", otp, clean_purpose(purpose), settings.otp_max_attempts
    )
