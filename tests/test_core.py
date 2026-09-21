import datetime as dt
import json

from lozatron.core import DeliveryState, Story, render_email, select_stories
from lozatron.apify import SpendState
from lozatron.gmail import cc_recipients

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def story(title: str, hours: int, url: str = "https://example.com/a") -> Story:
    return Story(
        title=title,
        url=url,
        source="Test",
        published_at=NOW - dt.timedelta(hours=hours),
        summary="Creator economy business update",
    )


def test_selects_fresh_creator_business_story():
    rows = [story("YouTube creator signs major brand partnership", 2)]
    selected = select_stories(rows, set(), now=NOW, window_hours=24, limit=10)
    assert [item.title for item in selected] == [rows[0].title]


def test_rejects_stale_and_duplicate():
    fresh = story("Creator launches subscription platform", 2)
    stale = story("Creator launches new brand deal", 60, "https://example.com/b")
    selected = select_stories([fresh, stale], {fresh.key}, now=NOW, window_hours=48, limit=10)
    assert selected == []


def test_rejects_tv_series_creator_false_positive():
    row = story("Series creator says farewell after Netflix show ends", 2)
    row.summary = "The drama series creator thanked fans after five seasons."
    assert select_stories([row], set(), now=NOW, window_hours=48, limit=10) == []


def test_rejects_roundups_and_job_listings():
    roundup = story("Top 10 influencer marketing platforms", 2)
    internship = story("Influencer marketing internship at a creator platform", 2, "https://example.com/job")
    assert select_stories([roundup, internship], set(), now=NOW, window_hours=48, limit=10) == []


def test_breaking_email_has_links():
    row = story("TikTok creator raises funding", 1)
    subject, text, html = render_email("breaking", [row], NOW)
    assert "Breaking" in subject
    assert row.url in text
    assert row.url in html


def test_state_round_trip(tmp_path):
    path = tmp_path / "delivered.json"
    row = story("Creator signs licensing deal", 1)
    state = DeliveryState(path).load()
    state.mark([row], NOW)
    state.save()
    assert row.key in DeliveryState(path).load().keys()
    assert json.loads(path.read_text())["version"] == 2
    assert json.loads(path.read_text())["last_success_date"] == "2026-09-20"


def test_paid_source_cap_is_fail_closed(tmp_path):
    state = SpendState(tmp_path / "spend.json", "2026-09-20").load()
    assert state.permits("youtube_community", 1.00)
    state.record("youtube_community")
    assert not state.permits("reddit_creator", 1.00)


def test_cc_recipients_are_optional_and_comma_separated(monkeypatch):
    monkeypatch.delenv("LOZ_CC_EMAILS", raising=False)
    assert cc_recipients() == []
    monkeypatch.setenv("LOZ_CC_EMAILS", "one@example.com, two@example.com")
    assert cc_recipients() == ["one@example.com", "two@example.com"]
