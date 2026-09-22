from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Iterable

from . import http
from .core import Story, parse_datetime, utcnow

# Trade publications. Lauren's rule: these CONFIRM a story, they are not the
# discovery layer. Every URL here was fetched and parsed before being added;
# Marketing Brew, Ad Age, PR Newswire and Business Wire were all in the historical
# configuration and are all dead or wrong, which is why nothing goes in unprobed.
FEEDS = (
    ("Tubefilter", "https://www.tubefilter.com/feed/"),
    ("Deadline", "https://deadline.com/feed/"),
    ("Variety", "https://variety.com/feed/"),
    ("Digiday", "https://digiday.com/rss"),
    ("Hollywood Reporter", "https://www.hollywoodreporter.com/feed/"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("NetInfluencer", "https://www.netinfluencer.com/feed/"),
    ("Podnews", "https://podnews.net/feed"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Passionfruit", "https://passionfru.it/feed/"),
    ("YouTube Blog", "https://blog.youtube/rss/"),
    ("Spotify Newsroom", "https://newsroom.spotify.com/feed/"),
    ("Adweek", "https://www.adweek.com/feed/"),
    ("Social Media Today", "https://www.socialmediatoday.com/feeds/news/"),
    ("Influencer Marketing Hub", "https://influencermarketinghub.com/feed/"),
    ("Kajabi", "https://kajabi.com/blog/rss.xml"),
    # Added 2026-09-22 after the free primary layer was removed, to replace the
    # volume it was supplying with sources that actually clear the mandate
    # gate. Both probed live first.
    ("Glossy", "https://www.glossy.co/feed/"),
)

# Probed and rejected, recorded so nobody re-probes them:
#   The Publish Press   -- no public RSS; /feed, /rss and the beehiiv paths 404
#   Colin and Samir     -- no public RSS
#   Marketing Brew      -- no public RSS (the historical entry was always a 404)
#   Creator Economy NYC -- no public RSS
#   ICYMI (Lia Haberman)-- feed exists but carries one stale placeholder item
#   Creator Handbook    -- parses from a residential IP and 403s from a GitHub
#                          Actions runner whatever user agent is sent, so it
#                          would be a permanent named error in every run
#   Social Media Examiner, Modern Retail -- parse fine, produce nothing that
#                          clears the mandate gate; pool noise only

# Removed on evidence, 2026-09-22: YouTube channel RSS and subreddit RSS.
#
# Both were added to honour Lauren's recorded "primary-first" rule. Measured
# over two days they degraded the brief rather than improving it:
#
#   Reddit contributed 125 of 373 pool items and close to zero signal. Nothing
#   it surfaced was ever corroborated elsewhere, which is the bar her own rule
#   sets ("signal, never proof"), so it only ever added volume: tech-support
#   questions, beginner advice and streamer drama.
#
#   YouTube channel RSS returns a channel's last fifteen uploads regardless of
#   age, so items were weeks stale, the business-term filter matched on "book"
#   and "million", and the one item that reached Lauren was a sponsored post.
#
# The primary layer that works is the paid one -- the X scraper and the YouTube
# community-posts actor in apify.py, which carry a real freshness field. Free
# RSS is not a substitute for it, and pretending otherwise is what produced the
# flood. Do not reinstate either without evidence they corroborate.

NEWS_QUERIES = (
    "creator economy OR creator monetization OR influencer marketing",
    "YouTube creator deal OR TikTok creator monetization OR creator brand partnership",
)


# Podnews alone ships ~2.3 MB, and the previous 2 MB cap truncated it mid-XML
# on every run, which surfaced only as an opaque ParseError.
FEED_READ_LIMIT = 12_000_000


def _fetch(url: str, *, timeout: int = 20) -> bytes:
    """Fetch a feed. Reads are idempotent, so transient faults retry."""
    return http.request(url, timeout=timeout, retries=2, read_limit=FEED_READ_LIMIT)


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _first(node: ET.Element, names: Iterable[str]) -> ET.Element | None:
    """First child matching `names`, in the order `names` gives them.

    The original scanned in document order against a set, so an Atom entry
    carrying both <updated> and <published> yielded whichever appeared first in
    the XML rather than the one we actually wanted. For the date lookup that
    silently substituted a modification time for the publication time, which is
    the input to every freshness decision in the system.
    """
    by_tag: dict[str, ET.Element] = {}
    for child in node.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        by_tag.setdefault(tag, child)
    for name in names:
        if (found := by_tag.get(name.lower())) is not None:
            return found
    return None


def _parse_feed(source: str, url: str, current: dt.datetime, tier: str,
                limit: int = 40) -> list[Story]:
    # `.lstrip()` is load-bearing. ElementTree refuses bytes with anything
    # before the XML declaration -- "XML or text declaration not at start of
    # entity" -- and Nieman Lab, among others, serves a leading newline. That
    # surfaced only as the word "ParseError" in a swallowed error list, so a
    # perfectly good feed looked dead for as long as it was in the list.
    root = ET.fromstring(_fetch(url).lstrip())
    entries = [node for node in root.iter()
               if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    out: list[Story] = []
    for entry in entries[:limit]:
        title = _text(_first(entry, ("title",)))
        summary = _text(_first(entry, ("description", "summary", "content")))
        date_text = _text(_first(entry, ("published", "pubdate", "date", "updated")))
        published = parse_datetime(date_text)
        link_node = _first(entry, ("link",))
        link = "" if link_node is None else (link_node.attrib.get("href") or _text(link_node))
        if title and link and published and published <= current + dt.timedelta(hours=1):
            out.append(Story(title=title, url=link, source=source,
                             published_at=published, summary=summary, tier=tier))
    return out


def rss_stories(now: dt.datetime | None = None) -> tuple[list[Story], list[str]]:
    current = now or utcnow()
    rows: list[Story] = []
    errors: list[str] = []
    for source, url in FEEDS:
        try:
            rows.extend(_parse_feed(source, url, current, "trade"))
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
    return rows, errors


def newsapi_stories(now: dt.datetime | None = None) -> tuple[list[Story], list[str]]:
    api_key = os.environ.get("NEWSAPI_KEY", "").strip()
    if not api_key:
        return [], []
    current = now or utcnow()
    rows: list[Story] = []
    errors: list[str] = []
    for query in NEWS_QUERIES:
        params = urllib.parse.urlencode({
            "q": query,
            "from": (current - dt.timedelta(hours=48)).isoformat(),
            "sortBy": "publishedAt",
            "pageSize": 50,
            "language": "en",
            "apiKey": api_key,
        })
        try:
            payload = http.request_json(f"https://newsapi.org/v2/everything?{params}", timeout=15, retries=2)
            for item in payload.get("articles", []):
                published = parse_datetime(item.get("publishedAt"))
                if published and item.get("title") and item.get("url"):
                    rows.append(Story(
                        title=item["title"],
                        url=item["url"],
                        source=(item.get("source") or {}).get("name") or "NewsAPI",
                        published_at=published,
                        summary=item.get("description") or "",
                    ))
        except Exception as exc:
            errors.append(f"NewsAPI: {type(exc).__name__}")
    return rows, errors


def collect(now: dt.datetime | None = None) -> tuple[list[Story], list[str]]:
    """Trade feeds, then NewsAPI.

    The free primary layer was removed; see the note above the source lists.
    Paid primary sources are collected separately in `apify.collect_paid` and
    joined by the caller, so the escalation path is intact.
    """
    rss_rows, rss_errors = rss_stories(now)
    news_rows, news_errors = newsapi_stories(now)
    return rss_rows + news_rows, rss_errors + news_errors

