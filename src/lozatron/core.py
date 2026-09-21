from __future__ import annotations

import dataclasses
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
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
    "deal", "partner*", "partnership", "acqui*", "fund*", "launch", "brand",
    "sponsor*", "collab", "invest*", "contract", "exclusive",
    "revenue", "monetiz*", "monetis*", "marketplace", "platform", "payout", "affiliate",
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


# Feeds append this when they truncate a description.
_RSS_TRUNCATION = re.compile(r"\s*\[(?:\u2026|\.\.\.)\]\s*$")
_TAG = re.compile(r"<[^>]+>")


def clean_feed_text(value: str, limit: int = 600) -> str:
    """Turn a raw feed description into plain readable prose.

    Feed summaries arrive already containing HTML entities and markup. Escaping
    them directly turns `&#8217;` into `&amp;#8217;`, which renders as the
    literal characters `&#8217;` in the inbox -- the glitch Lauren reported.
    Unescape first, strip tags, then let the renderer escape once.
    """
    text = _TAG.sub(" ", str(value or ""))
    text = html.unescape(text)
    # Unescaping can expose a second layer, and some feeds double-encode.
    if "&" in text:
        text = html.unescape(text)
    text = _RSS_TRUNCATION.sub("", re.sub(r"\s+", " ", text).strip())
    return text[:limit].strip()


def term_matcher(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Compile vocabulary into a word-boundary matcher.

    Plain `in` matching is why a laptop review reached the brief: "kick" (the
    streaming platform) matched "kickstand", and "partner" matched a hardware
    partnership. The same flaw let "book" match "Read My Book" and "tour" match
    "tourism".

    A term ending in `*` keeps prefix matching, which several deliberately need
    ("monetiz*", "monetis*" has to catch monetize, monetise and monetization). Everything
    else must match as a whole word.
    """
    parts = []
    for term in terms:
        if term.endswith("*"):
            parts.append(re.escape(term[:-1]))
        else:
            parts.append(re.escape(term) + r"\b")
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.IGNORECASE)


def env_text(name: str, default: str = "") -> str:
    """Read an environment variable, treating empty as unset.

    GitHub renders an unset repository variable as an empty string rather than
    omitting it, so `os.environ.get(name, default)` returns "" and the default
    never applies. Every setting in this system is read through these helpers
    for that reason.
    """
    return (os.environ.get(name) or "").strip() or default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_text(name, str(default)))
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_text(name, str(default)))
    except ValueError:
        return default


def env_flag(name: str) -> bool:
    return env_text(name).lower() == "true"


def env_list(*names: str) -> list[str]:
    """First non-empty comma-separated variable among `names`, split and cleaned."""
    for name in names:
        raw = env_text(name)
        if raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
    return []


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


# Campaign and click-tracking parameters identify the referrer, not the article.
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "gbraid", "wbraid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "ref", "ref_src", "source", "src", "cmpid", "ncid", "at_medium", "at_campaign",
    "__twitter_impression", "_hsenc", "_hsmi", "vgo_ee", "sh",
})

# Both fingerprint schemes are written while this date stands, so the ledger
# entries made before the query-string fix keep suppressing. Remove the v1
# fallback and this constant after 2026-12-20, one full prune() cycle on.
DUAL_KEY_UNTIL = dt.date(2026, 12, 20)


def normalize_url(value: str, *, keep_query: bool = True) -> str:
    """Canonical form of an article URL.

    The original discarded the query string outright, which is fine for a site
    using path-based article URLs and silently catastrophic for one using
    `?p=12345`: every article on that host collapsed to a single fingerprint, so
    exactly one story from it was ever deliverable and the rest were marked as
    duplicates. Identifying parameters are now kept and tracking noise dropped.

    `keep_query=False` reproduces the pre-fix behaviour byte for byte.
    """
    try:
        parts = urllib.parse.urlsplit(value.strip())
        host = parts.netloc.lower().removeprefix("www.")
        path = parts.path.rstrip("/") or "/"
        query = ""
        if keep_query and parts.query:
            kept = sorted(
                (key, val)
                for key, val in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
                if key.lower() not in TRACKING_PARAMS
            )
            query = urllib.parse.urlencode(kept)
        return urllib.parse.urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))
    except ValueError:
        return value.strip().lower()


def _digest(basis: str, title: str) -> str:
    material = basis or re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def fingerprint(title: str, url: str) -> str:
    return _digest(normalize_url(url), title)


def fingerprint_v1(title: str, url: str) -> str:
    """The pre-fix fingerprint. Frozen: it is the shape of the live ledger."""
    return _digest(normalize_url(url, keep_query=False), title)


@dataclasses.dataclass(slots=True)
class Story:
    title: str
    url: str
    source: str
    published_at: dt.datetime
    summary: str = ""
    score: int = 0
    # "primary" = the creator, platform or agency said it themselves.
    # "community" = Reddit chatter: signal, never proof on its own.
    # "trade" = a publication reporting it.
    tier: str = "trade"

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
            "tier": self.tier,
        }


# The wide net used when the analyst is scoring relevance. Its job is to bound
# cost and keep plainly off-topic material out, NOT to decide the brief -- that
# is what the narrow CREATOR_TERMS gate was doing, and a 22-phrase tuple deciding
# what a President reads is the ceiling on how good this can get.
DOMAIN_TERMS = (
    "creator", "creators", "influencer", "influencers", "ugc", "streamer", "streaming",
    "youtube", "tiktok", "instagram", "snapchat", "twitch", "kick", "substack",
    "patreon", "spotify", "podcast*", "podcaster", "newsletter", "shorts", "reels",
    "subscriber", "subscription", "monetiz*", "monetis*", "sponsorship", "brand deal", "creator fund",
    "talent agency", "mcn", "fan", "audience", "social video", "short-form",
    "roblox", "discord", "onlyfans", "beehiiv", "linktree", "gumroad", "whatnot",
    "social media", "digital media", "advertis*", "affiliate", "merch", "storefront",
    # Rule 7, from a named past miss: sports, fitness and lifestyle creator
    # brands, creator-led leagues and event/IP expansions are creator-business
    # stories. Filing them as sport is how Good Good Golf was dropped.
    "creator-led", "league", "tour", "golf", "fitness", "wellness", "esports",
    "team owner", "franchise", "live event", "festival", "residency",
)

# Freshness is arithmetic and stays in code, always. This is the June 2026
# crisis encoded: a rule that lives in prompt text is not a rule.
JUNK_TITLE = re.compile(r"\btop\s+\d+\b")
JUNK_PHRASES = ("internship", "job opening", "apply now")


_DOMAIN = None
_BUSINESS = None
_SOFT = None
_STRONG = None
_CREATOR = None


def _matchers():
    global _DOMAIN, _BUSINESS, _SOFT, _STRONG, _CREATOR
    if _DOMAIN is None:
        _DOMAIN = term_matcher(DOMAIN_TERMS)
        _BUSINESS = term_matcher(BUSINESS_TERMS)
        _SOFT = term_matcher(SOFT_PATTERNS)
        _STRONG = term_matcher(STRONG_TERMS)
        _CREATOR = term_matcher(CREATOR_TERMS)
    return _DOMAIN, _BUSINESS, _SOFT, _STRONG, _CREATOR


def passes_hard_gates(story: Story, now: dt.datetime, window_hours: int) -> bool:
    """Non-negotiable, model-independent rejection. Freshness and obvious junk."""
    age = (now - story.published_at).total_seconds() / 3600
    if age < -1 or age > window_hours:
        return False
    title = story.title.lower()
    if JUNK_TITLE.search(title) or any(term in title for term in JUNK_PHRASES):
        return False
    return True


def candidate(story: Story, now: dt.datetime, window_hours: int) -> bool:
    """Wide pre-filter for the analyst path.

    Passing this does not mean a story ships. It means the story is in the
    domain at all and is worth the tokens to score. Code still applies the
    relevance threshold and the limit afterwards, so the model can never add a
    story that failed a hard gate and never decides how many ship.

    Primary sources skip the vocabulary check. A creator announcing their own
    Netflix deal does not say the word "creator", and requiring it filtered out
    every single primary signal -- the layer Lauren ranks first. Those items
    already passed a business-term filter at ingest, which is the check that
    belongs to them.
    """
    if not passes_hard_gates(story, now, window_hours):
        return False
    if story.tier == "primary":
        return True
    domain, _, _, _, _ = _matchers()
    return bool(domain.search(f"{story.title} {story.summary}"))


def eligible(story: Story, now: dt.datetime, window_hours: int) -> bool:
    """The deterministic relevance gate, written from Lauren's rule 4.

    Include deals, funding, acquisitions, platform economics, monetization,
    creator-company launches, agencies, talent contracts, regulation with
    commercial impact and major brand partnerships. Reject generic interviews,
    culture commentary, routine tips, listicles and profiles unless there is a
    fresh business event.

    The topic half uses the wide DOMAIN_TERMS rather than the old 22-phrase
    CREATOR_TERMS tuple, which was rejecting 38% of everything fresh on a
    singular-versus-plural technicality. The business half stays mandatory,
    which is what keeps the widening from turning into noise.
    """
    if not passes_hard_gates(story, now, window_hours):
        return False
    text = f"{story.title} {story.summary}".lower()
    # A creator's own announcement is already business-filtered at ingest and
    # does not have to say the word "creator" to be creator news.
    if story.tier == "primary":
        return True
    if not any(term in text for term in DOMAIN_TERMS):
        return False
    if not any(term in text for term in BUSINESS_TERMS):
        return False
    if any(term in text for term in SOFT_PATTERNS) and not any(term in text for term in STRONG_TERMS):
        return False
    return True


def rank(story: Story, now: dt.datetime) -> int:
    text = f"{story.title} {story.summary}".lower()
    age = max(0.0, (now - story.published_at).total_seconds() / 3600)
    score = 12 if age <= 3 else 9 if age <= 8 else 5 if age <= 24 else 1
    _, _, _, strong, _ = _matchers()
    score += 2 * len(set(strong.findall(text)))
    score += 1 if re.search(r"\b(creator|youtube|tiktok|influencer)", text, re.I) else 0
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
    """The delivery ledger: which stories have shipped, and which slots.

    Written only after Gmail returns a message id, which is what makes it
    trustworthy. Schema version 2 adds `slots`; version 1 files load unchanged
    and simply carry no slot history, so no migration is required.
    """

    SCHEMA_VERSION = 2
    SLOT_RETENTION_DAYS = 30

    def __init__(self, path: Path):
        self.path = path
        self.rows: dict[str, str] = {}
        self.slots: dict[str, str] = {}
        self.last_success_date = ""

    def load(self) -> "DeliveryState":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.rows = dict(data.get("delivered", {}))
            self.slots = dict(data.get("slots", {}))
            self.last_success_date = str(data.get("last_success_date", ""))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            self.rows = {}
            self.slots = {}
            self.last_success_date = ""
        return self

    def keys(self) -> set[str]:
        return set(self.rows)

    def contains(self, story: "Story", *, today: dt.date | None = None) -> bool:
        """Has this story already been delivered, under either fingerprint?

        The v1 check is what makes the query-string fix a non-event: entries
        written before it keep matching, so nothing Lauren has already read is
        re-sent on rollout.
        """
        if story.key in self.rows:
            return True
        if (today or utcnow().date()) <= DUAL_KEY_UNTIL:
            return fingerprint_v1(story.title, story.url) in self.rows
        return False

    def delivered_slots(self) -> set[str]:
        return set(self.slots)

    def record_slot(self, slot: str, when: dt.datetime) -> None:
        self.slots[slot] = when.astimezone(UTC).isoformat()

    def hours_since_success(self, when: dt.datetime) -> float | None:
        """Age of the newest recorded slot, for the staleness watchdog.

        Returns None when nothing has ever been delivered, which a caller must
        treat differently from a fresh delivery.
        """
        stamps = [parse_datetime(value) for value in self.slots.values()]
        latest = max((item for item in stamps if item is not None), default=None)
        if latest is None:
            return None
        return (when.astimezone(UTC) - latest).total_seconds() / 3600

    def mark(self, stories: Iterable[Story], when: dt.datetime) -> None:
        """Record delivery. Writes both fingerprints during the dual-key window."""
        stamp = when.astimezone(UTC).isoformat()
        moment = when.astimezone(UTC)
        self.last_success_date = moment.date().isoformat()
        dual = moment.date() <= DUAL_KEY_UNTIL
        for story in stories:
            self.rows[story.key] = stamp
            if dual:
                self.rows[fingerprint_v1(story.title, story.url)] = stamp

    def prune(self, when: dt.datetime, days: int = 90) -> None:
        cutoff = when - dt.timedelta(days=days)
        kept: dict[str, str] = {}
        for key, value in self.rows.items():
            parsed = parse_datetime(value)
            if parsed is None or parsed >= cutoff:
                kept[key] = value
        self.rows = kept
        slot_cutoff = when - dt.timedelta(days=self.SLOT_RETENTION_DAYS)
        self.slots = {
            key: value
            for key, value in self.slots.items()
            if (parsed := parse_datetime(value)) is None or parsed >= slot_cutoff
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.SCHEMA_VERSION,
            "last_success_date": self.last_success_date,
            "delivered": dict(sorted(self.rows.items())),
            "slots": dict(sorted(self.slots.items())),
        }
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
        summary = clean_feed_text(story.summary, 500)
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
