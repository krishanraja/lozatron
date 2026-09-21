from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from . import http
from .core import Story, parse_datetime, utcnow

PROFILES: dict[str, dict[str, Any]] = {
    "x_creator": {"actor": "apidojo~tweet-scraper", "daily_cap": 4, "estimate_usd": 0.007},
    "reddit_creator": {"actor": "trudax~reddit-scraper-lite", "daily_cap": 2, "estimate_usd": 0.18},
    "youtube_community": {"actor": "lurkapi~youtube-community-posts-scraper", "daily_cap": 1, "estimate_usd": 0.85},
}

X_QUERY = (
    "(from:MrBeast OR from:KSI OR from:Sidemen OR from:ColinandSamir OR "
    "from:MKBHD OR from:LoganPaul OR from:DudePerfect OR from:emmachamberlain OR "
    "from:alexandracooper OR from:hankgreen OR from:YTCreators OR from:TikTokBusiness) "
    "(creator OR partnership OR monetization OR payout OR launch OR deal OR signed) since:{since}"
)

REDDIT_URLS = [
    "https://www.reddit.com/r/CreatorEconomy/new/",
    "https://www.reddit.com/r/PartneredYoutube/new/",
    "https://www.reddit.com/r/InfluencerMarketing/new/",
    "https://www.reddit.com/r/Substack/new/",
]

YOUTUBE_URLS = [
    "https://www.youtube.com/@MrBeast/community",
    "https://www.youtube.com/@KSI/community",
    "https://www.youtube.com/@ColinandSamir/community",
    "https://www.youtube.com/@MKBHD/community",
    "https://www.youtube.com/@Sidemen/community",
    "https://www.youtube.com/@DudePerfect/community",
]


class SpendState:
    """Apify spend ledger.

    Two corrections over the original. First, `save()` used to write only the
    rows it had loaded for today, which silently erased every prior day on every
    write and left no auditable cost trail. It now keeps a retention window.

    Second, a run is recorded *before* it is dispatched, not after. Apify bills
    from the moment an actor starts, so recording on return meant a timeout
    billed real money and left no trace -- and the next run, reading a lower
    total, would happily launch it again. Reserving first makes the cap
    fail-closed on the timeout path as well as the happy path.
    """

    RETENTION_DAYS = 45

    def __init__(self, path: Path, day: str | None = None):
        self.path = path
        self.day = day or utcnow().date().isoformat()
        self.all_runs: list[dict[str, Any]] = []

    @property
    def runs(self) -> list[dict[str, Any]]:
        """Today's rows. A view over the full ledger, not a replacement for it."""
        return [row for row in self.all_runs if row.get("date") == self.day]

    def load(self) -> "SpendState":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            rows = data.get("runs", [])
            self.all_runs = [row for row in rows if isinstance(row, dict)]
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self.all_runs = []
        return self

    def spent(self) -> float:
        return round(sum(float(row.get("estimated_usd", 0)) for row in self.runs), 4)

    def count(self, profile: str) -> int:
        return sum(1 for row in self.runs if row.get("profile") == profile)

    def permits(self, profile: str, daily_usd_cap: float) -> bool:
        cfg = PROFILES[profile]
        return self.count(profile) < int(cfg["daily_cap"]) and self.spent() + float(cfg["estimate_usd"]) <= daily_usd_cap

    def record(self, profile: str, *, status: str = "reserved") -> int:
        """Reserve budget for a run. Returns the row index for `settle`."""
        self.all_runs.append({
            "date": self.day,
            "profile": profile,
            "estimated_usd": PROFILES[profile]["estimate_usd"],
            "status": status,
        })
        return len(self.all_runs) - 1

    def settle(self, row_id: int, *, status: str) -> None:
        """Record the outcome of a reserved run. Cost is never refunded."""
        if 0 <= row_id < len(self.all_runs):
            self.all_runs[row_id]["status"] = status

    def prune(self) -> None:
        cutoff = (dt.date.fromisoformat(self.day) - dt.timedelta(days=self.RETENTION_DAYS)).isoformat()
        self.all_runs = [row for row in self.all_runs if str(row.get("date", "")) >= cutoff]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.prune()
        payload = {"version": 1, "runs": sorted(self.all_runs, key=lambda row: (row.get("date", ""), row.get("profile", "")))}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _input(profile: str, now: dt.datetime) -> dict[str, Any]:
    if profile == "x_creator":
        return {"searchTerms": [X_QUERY.format(since=(now - dt.timedelta(days=1)).date())], "maxTweets": 30, "addUserInfo": True}
    if profile == "reddit_creator":
        return {
            "startUrls": [{"url": url} for url in REDDIT_URLS],
            "skipComments": True,
            "skipUserPosts": True,
            "includeNSFW": False,
            "maxItems": 24,
            "sort": "new",
            "proxy": {"useApifyProxy": True},
        }
    if profile == "youtube_community":
        return {"urls": YOUTUBE_URLS, "maxItems": 3}
    raise KeyError(profile)


def _request(method: str, url: str, token: str, body: dict[str, Any] | None = None, timeout: int = 150) -> Any:
    """Call the Apify API.

    The token travels in an Authorization header rather than the query string,
    where it would otherwise be captured by Apify access logs, any proxy in
    between, and any exception message that echoes the URL.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return http.request_json(url, method=method, data=data, headers=headers, timeout=timeout)


def _run_profile(profile: str, token: str, now: dt.datetime) -> list[dict[str, Any]]:
    actor = urllib.parse.quote(PROFILES[profile]["actor"], safe="")
    run = _request(
        "POST",
        f"https://api.apify.com/v2/acts/{actor}/runs?waitForFinish=120",
        token,
        _input(profile, now),
    )
    run_data = run.get("data") or {}
    # A RUNNING actor has not finished writing its dataset. Reading it anyway
    # yields a partial result that then gets recorded as a complete run -- paid
    # for in full, acted on in part. Only a terminal SUCCEEDED counts.
    if run_data.get("status") != "SUCCEEDED":
        return []
    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        return []
    items = _request(
        "GET",
        f"https://api.apify.com/v2/datasets/{urllib.parse.quote(dataset_id)}/items?format=json&clean=true",
        token,
        timeout=60,
    )
    return items if isinstance(items, list) else []


def _as_story(profile: str, row: dict[str, Any]) -> Story | None:
    title = str(row.get("title") or row.get("text") or row.get("body") or row.get("content") or "").strip()
    url = str(row.get("url") or row.get("postUrl") or row.get("tweetUrl") or row.get("link") or "").strip()
    published = parse_datetime(str(row.get("createdAt") or row.get("publishedAt") or row.get("date") or row.get("timestamp") or ""))
    if not title or not url or published is None:
        return None
    summary = str(row.get("description") or row.get("text") or row.get("body") or "")
    return Story(title=title[:500], url=url, source=f"Apify/{profile}", published_at=published, summary=summary[:1000])


def collect_paid(spend_path: Path, now: dt.datetime | None = None) -> tuple[list[Story], list[str], bool]:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return [], [], False
    current = now or utcnow()
    cap = float(os.environ.get("LOZ_APIFY_DAILY_USD_CAP", "1.00"))
    state = SpendState(spend_path, current.date().isoformat()).load()
    stories: list[Story] = []
    errors: list[str] = []
    changed = False
    for profile in PROFILES:
        if not state.permits(profile, cap):
            continue
        row_id = state.record(profile)
        changed = True
        try:
            rows = _run_profile(profile, token, current)
            state.settle(row_id, status="succeeded")
            stories.extend(item for row in rows if (item := _as_story(profile, row)) is not None)
        except Exception as exc:
            # The reservation stands. The actor may well have run and billed.
            state.settle(row_id, status="failed")
            errors.append(f"Apify/{profile}: {type(exc).__name__}")
    if changed:
        state.save()
    return stories, errors, changed

