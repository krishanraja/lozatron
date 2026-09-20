from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from email.message import EmailMessage


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
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Google did not return an access token")
    return token


def recipients() -> list[str]:
    values = [item.strip() for item in _required("LOZ_RECIPIENT_EMAILS").split(",")]
    return [item for item in values if item]


def send(subject: str, text_body: str, html_body: str) -> str:
    sender = _required("GOOGLE_SENDER_EMAIL")
    to = recipients()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    body = json.dumps({"raw": raw}).encode("utf-8")
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {refresh_access_token()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    message_id = payload.get("id")
    if not message_id:
        raise RuntimeError("Gmail did not return a message id")
    return str(message_id)


def verify_credentials() -> None:
    token = refresh_access_token()
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("emailAddress"):
        raise RuntimeError("Gmail profile verification failed")

