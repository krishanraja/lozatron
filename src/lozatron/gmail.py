from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.parse
from email.message import EmailMessage
from email.utils import make_msgid

from . import http


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def refresh_access_token() -> str:
    data = urllib.parse.urlencode({
        "client_id": _required("GOOGLE_CLIENT_ID"),
        "client_secret": _required("GOOGLE_CLIENT_SECRET"),
        "refresh_token": _required("GOOGLE_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }).encode("utf-8")
    payload = http.request_json(
        "https://oauth2.googleapis.com/token",
        method="POST",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Google did not return an access token")
    return token


def recipients() -> list[str]:
    values = [item.strip() for item in _required("LOZ_RECIPIENT_EMAILS").split(",")]
    return [item for item in values if item]


def ops_recipients() -> list[str]:
    """Where operational mail goes. Never Lauren's address.

    Cost reports and alarms are Krish's business, not the reader's, so they
    fall back to the CC list rather than the brief's recipients.
    """
    raw = os.environ.get("LOZ_OPS_EMAILS", "") or os.environ.get("LOZ_CC_EMAILS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def cc_recipients() -> list[str]:
    values = [item.strip() for item in os.environ.get("LOZ_CC_EMAILS", "").split(",")]
    return [item for item in values if item]


def edition_message_id(edition_id: str, sender: str) -> str:
    """A deterministic RFC822 Message-ID derived from the edition.

    Gmail's send endpoint has no idempotency key, so this is how a resend can be
    told apart from a duplicate: the same edition always produces the same id,
    and `probe_sent` can ask Gmail whether that id already exists.
    """
    domain = sender.rsplit("@", 1)[-1] or "lozatron.local"
    digest = hashlib.sha256(f"{edition_id}|{sender}".encode("utf-8")).hexdigest()[:32]
    return f"<lozatron-{digest}@{domain}>"


def probe_sent(edition_id: str) -> str | None:
    """Return the Gmail id for this edition if it was already sent, else None.

    Used to resolve an ambiguous send failure without risking a second copy in
    Lauren's inbox. A probe that itself fails returns None, which the caller
    must treat as "unknown", never as "not sent".
    """
    sender = _required("GOOGLE_SENDER_EMAIL")
    query = urllib.parse.urlencode({"q": f"rfc822msgid:{edition_message_id(edition_id, sender)}"})
    try:
        payload = http.request_json(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{query}",
            headers={"Authorization": f"Bearer {refresh_access_token()}"},
            timeout=30,
        )
    except Exception:
        return None
    messages = payload.get("messages") or []
    return str(messages[0].get("id")) if messages else None


def send(subject: str, text_body: str, html_body: str, *, edition_id: str = "",
         to: list[str] | None = None, cc: list[str] | None = None) -> str:
    """Send the brief. Returns the Gmail message id.

    Deliberately NOT retried on read timeouts or server errors: either may mean
    the message was already accepted, and a blind resend puts the brief in
    Lauren's inbox twice. Only pre-send faults (DNS, connection refused) retry.
    On an ambiguous failure the edition's deterministic Message-ID is probed,
    so a genuine send is recognised rather than repeated.
    """
    sender = _required("GOOGLE_SENDER_EMAIL")
    to = to if to is not None else recipients()
    cc = cc if cc is not None else cc_recipients()
    if not to:
        raise RuntimeError("No recipients resolved for this message")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Message-ID"] = edition_message_id(edition_id, sender) if edition_id else make_msgid()
    if edition_id:
        message["X-Lozatron-Edition"] = edition_id
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    body = json.dumps({"raw": raw}).encode("utf-8")
    try:
        payload = http.request_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            method="POST",
            data=body,
            headers={
                "Authorization": f"Bearer {refresh_access_token()}",
                "Content-Type": "application/json",
            },
            timeout=30,
            idempotent=False,
        )
    except Exception:
        if edition_id and (existing := probe_sent(edition_id)):
            return existing
        raise
    message_id = payload.get("id")
    if not message_id:
        raise RuntimeError("Gmail did not return a message id")
    return str(message_id)


def verify_credentials() -> None:
    token = refresh_access_token()
    payload = http.request_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not payload.get("emailAddress"):
        raise RuntimeError("Gmail profile verification failed")
