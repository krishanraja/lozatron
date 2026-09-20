from __future__ import annotations

import dataclasses
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Iterable

UTC = dt.timezone.utc

CREATOR_TERMS = (
    "creator economy", "content creator", "digital creator", "influencer",
    "youtube creator", "youtuber", "tiktok creator", "twitch streamer",
    "substack", "patreon", "streamer", "mrbeast",
    "sidemen", "ksi", "logan paul", "emma chamberlain", "alex cooper",
    "colin and samir", "dude perfect", "creator economy",
)

BUSINESS_TERMS = (
    "deal", "partner", "partnership", "acqui", "fund", "launch", "brand",
    "sponsor", "collab", "invest", "contract", "exclusive",
    "revenue", "monetiz", "marketplace", "platform", "payout", "affiliate",
    "commerce", "licensing", "agency", "talent", "ceo", "cmo", "hired",
    "sold", "raised", "advertising", "subscription", "storefront",
)

SOFT_PATTERNS = (
    "how to", "tips", "guide", "opinion", "roundup", "best practices",
    "what is", "explainer", "checklist", "playbook",
)

STRONG_TERMS = (
    "acquisition", "acquires", "funding", "raised", "launch", "partnership",
    "payout", "monetization", "contract", "licensing", "signed", "appoints",
    "hires", "sold", "storefront",
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def normalize_url(value: str) -> str:
    try:
        parts = urllib.parse.urlsplit(value.strip())
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit((parts.scheme.lower() or "https", host, path, "", ""))
    except ValueError:
        return value.strip().lower()


def fingerprint(title: str, url: str) -> str:
    basis = normalize_url(url) or re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclasses.dataclass(slots=True)
class Story:
    title: str
    url: str
    source: str
    published_at: dt.datetime
    summary: str = ""
    score: int = 0

    @property
    def key(self) -> str:
        return fingerprint(self.title, self.url)

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "summary": self.summary,
            "score": self.score,
        }


def eligible(story: Story, now: dt.datetime, window_hours: int) -> bool:
    text = f"{story.title} {story.summary}".lower()
    title = story.title.lower()
    age = (now - story.published_at).total_seconds() / 3600
    if age < -1 or age > window_hours:
        return False
    has_creator_context = any(term in text for term in CREATOR_TERMS)
    has_creator_plural = re.search(r"(?<![a-z])creators(?![a-z])", text) is not None
    if not (has_creator_context or has_creator_plural):
        return False
    if not any(term in text for term in BUSINESS_TERMS):
        return False
    if re.search(r"\btop\s+\d+\b", title) or any(term in title for term in ("internship", "job opening", "apply now")):
        return False
    if any(term in text for term in SOFT_PATTERNS) and not any(term in text for term in STRONG_TERMS):
        return False
    return True


def rank(story: Story, now: dt.datetime) -> int:
    text = f"{story.title} {story.summary}".lower()
    age = max(0.0, (now - story.published_at).total_seconds() / 3600)
    score = 12 if age <= 3 else 9 if age <= 8 else 5 if age <= 24 else 1
    score += sum(2 for term in STRONG_TERMS if term in text)
    score += 1 if any(term in text for term in ("creator", "youtube", "tiktok", "influencer")) else 0
    return score


def select_stories(
    stories: Iterable[Story], delivered: set[str], *, now: dt.datetime,
    window_hours: int, limit: int,
) -> list[Story]:
    unique: dict[str, Story] = {}
    for story in stories:
        if story.key in delivered or not eligible(story, now, window_hours):
            continue
        story.score = rank(story, now)
        prior = unique.get(story.key)
        if prior is None or story.score > prior.score:
            unique[story.key] = story
    return sorted(unique.values(), key=lambda item: (-item.score, -item.published_at.timestamp()))[:limit]


class DeliveryState:
    def __init__(self, path: Path):
        self.path = path
        self.rows: dict[str, str] = {}

    def load(self) -> "DeliveryState":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.rows = dict(data.get("delivered", {}))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            self.rows = {}
        return self

    def keys(self) -> set[str]:
        return set(self.rows)

    def mark(self, stories: Iterable[Story], when: dt.datetime) -> None:
        stamp = when.astimezone(UTC).isoformat()
        for story in stories:
            self.rows[story.key] = stamp

    def prune(self, when: dt.datetime, days: int = 90) -> None:
        cutoff = when - dt.timedelta(days=days)
        kept: dict[str, str] = {}
        for key, value in self.rows.items():
            parsed = parse_datetime(value)
            if parsed is None or parsed >= cutoff:
                kept[key] = value
        self.rows = kept

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "delivered": dict(sorted(self.rows.items()))}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_email(mode: str, stories: list[Story], now: dt.datetime) -> tuple[str, str, str]:
    label = "Breaking creator-business update" if mode == "breaking" else "Creator-business briefing"
    subject = f"Lozatron | {label} | {now:%Y-%m-%d %H:%M} UTC"
    if not stories:
        text_body = f"{label}\n\nNo qualifying new stories were found."
        html_body = f"<h1>{html.escape(label)}</h1><p>No qualifying new stories were found.</p>"
        return subject, text_body, html_body

    text_lines = [label, ""]
    html_rows = [f"<h1>{html.escape(label)}</h1>"]
    for story in stories:
        age = max(0, round((now - story.published_at).total_seconds() / 3600))
        summary = re.sub(r"\s+", " ", story.summary).strip()[:500]
        text_lines.extend([
            story.title,
            f"{story.source}, {age}h old",
            summary,
            story.url,
            "",
        ])
        html_rows.append(
            "<article>"
            f"<h2><a href=\"{html.escape(story.url, quote=True)}\">{html.escape(story.title)}</a></h2>"
            f"<p><strong>{html.escape(story.source)}</strong>, {age}h old</p>"
            f"<p>{html.escape(summary)}</p>"
            "</article>"
        )
    return subject, "\n".join(text_lines), "\n".join(html_rows)
