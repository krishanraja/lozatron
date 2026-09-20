from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable

from .core import Story, parse_datetime, utcnow

FEEDS = (
    ("Tubefilter", "https://www.tubefilter.com/feed/"),
    ("Deadline", "https://deadline.com/feed/"),
    ("Variety", "https://variety.com/feed/"),
    ("Digiday", "https://digiday.com/rss"),
    ("Hollywood Reporter", "https://www.hollywoodreporter.com/feed/"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("NetInfluencer", "https://www.netinfluencer.com/feed/"),
    ("Marketing Brew", "https://www.marketingbrew.com/rss"),
    ("Podnews", "https://podnews.net/feed"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Passionfruit", "https://passionfru.it/feed/"),
    ("YouTube Blog", "https://blog.youtube/rss/"),
    ("Spotify Newsroom", "https://newsroom.spotify.com/feed/"),
)

NEWS_QUERIES = (
    "creator economy OR creator monetization OR influencer marketing",
    "YouTube creator deal OR TikTok creator monetization OR creator brand partnership",
)


def _fetch(url: str, *, timeout: int = 15) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Lozatron/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000)


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _first(node: ET.Element, names: Iterable[str]) -> ET.Element | None:
    wanted = {name.lower() for name in names}
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() in wanted:
            return child
    return None


def rss_stories(now: dt.datetime | None = None) -> tuple[list[Story], list[str]]:
    current = now or utcnow()
    rows: list[Story] = []
    errors: list[str] = []
    for source, url in FEEDS:
        try:
            root = ET.fromstring(_fetch(url))
            entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
            for entry in entries[:40]:
                title = _text(_first(entry, ("title",)))
                summary = _text(_first(entry, ("description", "summary", "content")))
                date_text = _text(_first(entry, ("pubdate", "published", "updated", "date")))
                published = parse_datetime(date_text)
                link_node = _first(entry, ("link",))
                link = "" if link_node is None else (link_node.attrib.get("href") or _text(link_node))
                if title and link and published and published <= current + dt.timedelta(hours=1):
                    rows.append(Story(title=title, url=link, source=source, published_at=published, summary=summary))
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
            payload = json.loads(_fetch(f"https://newsapi.org/v2/everything?{params}").decode("utf-8"))
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
    rss_rows, rss_errors = rss_stories(now)
    news_rows, news_errors = newsapi_stories(now)
    return rss_rows + news_rows, rss_errors + news_errors

