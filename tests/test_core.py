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


# --- the wide pre-filter for the analyst path ---

def test_hard_gates_reject_stale_regardless_of_topic():
    from lozatron.core import passes_hard_gates
    assert not passes_hard_gates(story("Creator signs brand deal", 60), NOW, 48)


def test_hard_gates_reject_listicles_and_job_ads():
    from lozatron.core import passes_hard_gates
    assert not passes_hard_gates(story("Top 10 creator platforms", 2), NOW, 48)
    assert not passes_hard_gates(story("Creator marketing internship open", 2), NOW, 48)


def test_on_topic_story_the_old_plural_rule_dropped_now_passes():
    """The old tuple matched `creators` plural only, so this was invisible.

    Both gates now admit it: the wide one for the analyst path, and the strict
    one since it was rewritten from Lauren's rule 4.
    """
    from lozatron.core import candidate
    row = story("YouTube debuts Canadian creator shows and shopping tools", 2)
    row.summary = "The platform launches a creator monetization programme in Canada."
    assert candidate(row, NOW, 48)
    assert select_stories([row], set(), now=NOW, window_hours=48, limit=10)


def test_candidate_still_excludes_plainly_off_domain_material():
    from lozatron.core import candidate
    row = story("Navy outlines shipbuilding budget for next decade", 2)
    row.summary = "Procurement plans for naval vessels."
    assert not candidate(row, NOW, 48)


def test_candidate_never_admits_what_a_hard_gate_rejected():
    """The pre-filter widens topic scope only. Freshness is not negotiable."""
    from lozatron.core import candidate
    row = story("YouTube creator monetization expands", 100)
    assert not candidate(row, NOW, 48)


def test_strict_gate_is_unchanged_and_remains_the_fallback():
    row = story("YouTube creator signs major brand partnership", 2)
    assert select_stories([row], set(), now=NOW, window_hours=48, limit=10)


# --- rule 7 must not admit the film festival circuit ---

def test_film_industry_news_is_not_creator_business():
    """A live run returned all three of these before the domain split."""
    from lozatron.core import candidate
    cases = [
        ("SCAD Savannah Film Festival to Open With 'A Talent for Murder'",
         "The festival will honor Helen Mirren with its Legend of Entertainment Award."),
        ("Netflix's Liz Franco Joins Amazon's Nonfiction Team",
         "The streamer has hired a Netflix exec as a Creative Executive on docuseries."),
        ("Camden Film Festival: 'Dependence' Takes Top Awards",
         "The documentary festival announced its awards after wrapping its 22nd edition."),
    ]
    for title, summary in cases:
        row = story(title, 2)
        row.summary = summary
        assert not candidate(row, NOW, 48), title
        assert not select_stories([row], set(), now=NOW, window_hours=48, limit=10), title


def test_rule_seven_still_admits_a_creator_led_league():
    """Good Good Golf is the named miss: creator-led sport IS creator business."""
    from lozatron.core import candidate
    row = story("Good Good Golf launches creator-led league with new tour sponsorship", 2)
    row.summary = "The creator-owned golf brand announced a league and a sponsorship deal."
    assert candidate(row, NOW, 48)
    assert select_stories([row], set(), now=NOW, window_hours=48, limit=10)


def test_a_commercial_term_alone_is_not_enough():
    from lozatron.core import candidate
    row = story("Retail chain expands its licensing partnership with a franchise operator", 2)
    row.summary = "The commercial agreement covers new storefront locations."
    assert not candidate(row, NOW, 48)
