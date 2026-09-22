from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from . import http
from .core import Story, env_float, parse_datetime, utcnow

# `estimate_usd` is only a pre-flight reservation. It is never the reported cost:
# Apify pricing drifts and these figures have never been checked against an
# invoice, so every run is reconciled afterwards against the authoritative
# `usageTotalUsd` on the run object. `max_charge_usd` is enforced by Apify
# itself, which is the only cap that holds when an actor misbehaves; the local
# ledger cap can only decide whether to start a run, never how much it bills.
PROFILES: dict[str, dict[str, Any]] = {
    "x_creator": {
        "actor": "apidojo~tweet-scraper", "daily_cap": 1,
        "estimate_usd": 0.007, "max_charge_usd": 0.05, "max_items": 30,
    },
    "reddit_creator": {
        "actor": "trudax~reddit-scraper-lite", "daily_cap": 1,
        "estimate_usd": 0.18, "max_charge_usd": 0.25, "max_items": 24,
    },
    "youtube_community": {
        "actor": "lurkapi~youtube-community-posts-scraper", "daily_cap": 1,
        "estimate_usd": 0.85, "max_charge_usd": 0.90, "max_items": 18,
    },
}

# A ceiling above the daily one, because thirty days at the daily cap is a bill
# nobody agreed to. Both are checked before any run starts.
DEFAULT_DAILY_CAP_USD = 1.00
DEFAULT_MONTHLY_CAP_USD = 8.00

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

    @staticmethod
    def _cost(row: dict[str, Any]) -> float:
        """What a row costs against the cap.

        The settled charge once Apify has reported one, the reservation until
        then. Never both, and never zero just because a run failed: a failed
        run still bills.
        """
        actual = row.get("actual_usd")
        if isinstance(actual, (int, float)):
            return float(actual)
        return float(row.get("estimated_usd", 0) or 0)

    def spent(self) -> float:
        return round(sum(self._cost(row) for row in self.runs), 4)

    def spent_month(self) -> float:
        month = self.day[:7]
        return round(
            sum(self._cost(row) for row in self.all_runs if str(row.get("date", "")).startswith(month)),
            4,
        )

    def spent_between(self, start: str, end: str) -> tuple[float, int]:
        """Settled spend and run count over an inclusive date range."""
        rows = [row for row in self.all_runs if start <= str(row.get("date", "")) <= end]
        return round(sum(self._cost(row) for row in rows), 4), len(rows)

    def count(self, profile: str) -> int:
        return sum(1 for row in self.runs if row.get("profile") == profile)

    def permits(self, profile: str, daily_usd_cap: float,
                monthly_usd_cap: float = DEFAULT_MONTHLY_CAP_USD) -> bool:
        """Three independent gates, all fail-closed.

        Run count per profile, spend today, and spend this month. The monthly
        gate exists because thirty days at the daily cap is a bill nobody
        agreed to.
        """
        cfg = PROFILES[profile]
        estimate = float(cfg["estimate_usd"])
        return (
            self.count(profile) < int(cfg["daily_cap"])
            and self.spent() + estimate <= daily_usd_cap
            and self.spent_month() + estimate <= monthly_usd_cap
        )

    def record(self, profile: str, *, status: str = "reserved") -> int:
        """Reserve budget for a run. Returns the row index for `settle`."""
        self.all_runs.append({
            "date": self.day,
            "profile": profile,
            "estimated_usd": PROFILES[profile]["estimate_usd"],
            "status": status,
        })
        return len(self.all_runs) - 1

    def settle(self, row_id: int, *, status: str, actual_usd: float | None = None,
               run_id: str = "") -> None:
        """Record the outcome of a reserved run.

        `actual_usd` replaces the estimate for cap arithmetic and reporting, so
        the weekly figure is what Apify charged rather than what this file
        guessed. Cost is never refunded: a failed run has still billed.
        """
        if 0 <= row_id < len(self.all_runs):
            row = self.all_runs[row_id]
            row["status"] = status
            if run_id:
                row["run_id"] = run_id
            if actual_usd is not None:
                row["actual_usd"] = round(float(actual_usd), 6)

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


@dataclasses.dataclass(slots=True)
class RunResult:
    items: list[dict[str, Any]]
    run_id: str = ""
    status: str = ""
    usd: float | None = None      # authoritative charge, None when unknown


def _actual_cost(run_data: dict[str, Any]) -> float | None:
    """The charge Apify reports for this run, not the figure we guessed."""
    for key in ("usageTotalUsd", "chargedTotalUsd"):
        value = run_data.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 6)
    return None


def _run_profile(profile: str, token: str, now: dt.datetime) -> RunResult:
    config = PROFILES[profile]
    actor = urllib.parse.quote(config["actor"], safe="")
    # maxTotalChargeUsd is the only ceiling Apify itself enforces. maxItems caps
    # charged rows for pay-per-result actors. Both are sent; neither substitutes
    # for the other, because they bound different pricing models.
    params = urllib.parse.urlencode({
        "waitForFinish": 120,
        "maxTotalChargeUsd": config["max_charge_usd"],
        "maxItems": config["max_items"],
    })
    run = _request(
        "POST",
        f"https://api.apify.com/v2/acts/{actor}/runs?{params}",
        token,
        _input(profile, now),
    )
    run_data = run.get("data") or {}
    run_id = str(run_data.get("id") or "")
    status = str(run_data.get("status") or "")

    # A RUNNING actor has not finished writing its dataset. Reading it anyway
    # yields a partial result that then gets recorded as a complete run -- paid
    # for in full, acted on in part. Only a terminal SUCCEEDED counts.
    if status != "SUCCEEDED":
        # It still billed for whatever it did, so refetch the real charge rather
        # than recording the estimate for a run that produced nothing.
        return RunResult([], run_id, status, _settled_cost(run_id, token))

    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        return RunResult([], run_id, status, _settled_cost(run_id, token))
    items = _request(
        "GET",
        f"https://api.apify.com/v2/datasets/{urllib.parse.quote(dataset_id)}/items?format=json&clean=true",
        token,
        timeout=60,
    )
    return RunResult(
        items if isinstance(items, list) else [],
        run_id,
        status,
        _settled_cost(run_id, token) or _actual_cost(run_data),
    )


def _settled_cost(run_id: str, token: str) -> float | None:
    """Refetch a finished run for its final charge.

    Cost is not final at the moment a run reports terminal state, so the figure
    on the start response can understate the bill. A failed read returns None
    and the reservation stands, which errs toward over-counting spend rather
    than under-counting it.
    """
    if not run_id:
        return None
    try:
        data = _request("GET", f"https://api.apify.com/v2/actor-runs/{urllib.parse.quote(run_id)}",
                        token, timeout=30)
        return _actual_cost(data.get("data") or {})
    except Exception:  # noqa: BLE001 - cost readback must never fail a run
        return None


# What each paid profile actually produces. Until this existed every paid story
# was tagged "trade" by omission, which had two consequences that only look
# small on paper:
#
#   `Cluster.confirmed` rejects a cluster whose members are all "community".
#   Reddit rows were arriving as "trade", so the guard written specifically to
#   stop uncorroborated Reddit reaching Lauren was protecting nothing. The
#   flood mechanism, sitting dormant behind a feature flag.
#
#   Social posts ranked as news. A post is commentary from one person; it is
#   not a report, and it must never sit in the numbered list next to a trade
#   story. "social" is handled by the commentary layer, never as an entry.
PROFILE_TIERS = {
    "x_creator": "social",
    "youtube_community": "social",
    "reddit_creator": "community",
}


def _as_story(profile: str, row: dict[str, Any]) -> Story | None:
    text = str(row.get("text") or row.get("body") or row.get("content") or "").strip()
    title = str(row.get("title") or "").strip()
    url = str(row.get("url") or row.get("postUrl") or row.get("tweetUrl") or row.get("link") or "").strip()
    published = parse_datetime(str(row.get("createdAt") or row.get("publishedAt") or row.get("date") or row.get("timestamp") or ""))
    tier = PROFILE_TIERS.get(profile, "community")

    # A post has no headline, and pretending its first 500 characters are one
    # is how "Making your first time simple #TidePartner" shipped to Lauren as
    # a breaking creator-business update. For social the text is the body and
    # the author is the label; the commentary layer never renders a headline.
    if tier == "social":
        author = str(row.get("author") or row.get("username") or row.get("authorName") or "").strip()
        if not text or not url or published is None:
            return None
        return Story(
            title=(author or profile)[:120], url=url, source=f"Apify/{profile}",
            published_at=published, summary=text[:1000], tier=tier,
        )

    title = title or text
    if not title or not url or published is None:
        return None
    summary = str(row.get("description") or text)
    return Story(title=title[:500], url=url, source=f"Apify/{profile}",
                 published_at=published, summary=summary[:1000], tier=tier)


def collect_paid(spend_path: Path, now: dt.datetime | None = None,
                 profiles: tuple[str, ...] | None = None) -> tuple[list[Story], list[str], bool]:
    """Run the paid profiles named, or all of them.

    `profiles` exists because the profiles no longer serve one purpose. The
    social ones supply commentary that annotates the news, so they are useful
    precisely on the days the news is good; the story ones substitute for a
    thin free pool. One "should we spend" answer cannot cover both.
    """
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return [], [], False
    wanted = tuple(profiles) if profiles is not None else tuple(PROFILES)
    current = now or utcnow()
    cap = env_float("LOZ_APIFY_DAILY_USD_CAP", DEFAULT_DAILY_CAP_USD)
    monthly_cap = env_float("LOZ_APIFY_MONTHLY_USD_CAP", DEFAULT_MONTHLY_CAP_USD)
    state = SpendState(spend_path, current.date().isoformat()).load()
    stories: list[Story] = []
    errors: list[str] = []
    changed = False
    for profile in wanted:
        if profile not in PROFILES:
            continue
        if not state.permits(profile, cap, monthly_cap):
            continue
        row_id = state.record(profile)
        changed = True
        try:
            result = _run_profile(profile, token, current)
            state.settle(
                row_id,
                status=result.status.lower() or "succeeded",
                actual_usd=result.usd,
                run_id=result.run_id,
            )
            stories.extend(
                item for row in result.items if (item := _as_story(profile, row)) is not None
            )
        except Exception as exc:
            # The reservation stands. The actor may well have run and billed,
            # and a run-creation POST is never retried blindly.
            state.settle(row_id, status="failed")
            errors.append(f"Apify/{profile}: {type(exc).__name__}")
    if changed:
        state.save()
    return stories, errors, changed

