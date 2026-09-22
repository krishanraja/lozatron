"""The mechanics track: how it works, not what happened.

Lauren is President of The Publish Press. A creator-news wire re-reports her
own product back to her, so this is the half of the brief she cannot get
anywhere else. It exists as a separate, capped section precisely so that
widening the brief toward mechanics cannot loosen the news list -- rule 4
still rejects explainers there, and rule 2 still forbids padding.
"""
from __future__ import annotations

import datetime as dt

from lozatron import app
from lozatron.brief import compose
from lozatron.cluster import MAX_ANALYSIS, select_analysis, select_clusters
from lozatron.core import ANALYSIS_WINDOW_HOURS, Story, eligible, eligible_analysis
from lozatron.render import render

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def essay(title, hours=20, source="A Media Operator", tier="analysis"):
    return Story(
        title=title, url=f"https://example.com/{abs(hash(title)) % 10**8}",
        source=source, published_at=NOW - dt.timedelta(hours=hours),
        summary="A long piece about how the business works.", tier=tier,
    )


# --- the gate is the right shape for essays ---

def test_a_mechanics_headline_passes_without_saying_creator():
    # The whole reason this track needs its own gate: these say "publishers"
    # and "monetize", never "creator", so the news headline rule rejects them.
    for title in ("To monetize more content, publishers must break down silos",
                  "Newsquest grows profit as online subscribers rise to 159,000",
                  "Jacobs Media Separated Profitable Events Brand From Rest of Business",
                  "How a niche magazine turned print into a competitive advantage"):
        item = essay(title)
        assert eligible_analysis(item, NOW), title
        assert not eligible(item, NOW, 48), f"should not reach the news list: {title}"


def test_trackers_and_diaries_are_rejected():
    """A publication's furniture updates forever and would reappear daily."""
    for title in ("Journalism job cuts in 2026 tracked: Latest from Reach",
                  "News diary 21-27 September: Burnham to meet Trump",
                  "AI in journalism: Live tracker of scandals and mistakes",
                  "DCN's media industry must reads: week of September 17"):
        assert not eligible_analysis(essay(title), NOW), title


def test_only_the_analysis_tier_can_use_this_gate():
    """A trade story must not sneak past rule 4 by routing through mechanics."""
    item = essay("How publishers build leverage in the AI era", tier="trade")
    assert not eligible_analysis(item, NOW)


def test_the_window_is_a_week_not_two_days():
    assert ANALYSIS_WINDOW_HOURS == 168
    assert eligible_analysis(essay("How publishers grow subscription revenue", hours=140), NOW)
    assert not eligible_analysis(essay("How publishers grow subscription revenue", hours=200), NOW)


# --- it cannot pad or crowd the news ---

# Distinct subjects, so clustering does not collapse them into one event.
DISTINCT = (
    "To monetize more content, publishers must break down silos",
    "Newsquest grows profit as online subscribers rise to 159,000",
    "Jacobs Media Separated Profitable Events Brand From Rest of Business",
    "How a niche magazine turned print into a competitive advantage",
    "Buying Traffic Isn't the Problem. Buying the Wrong Traffic Is",
    "The Atlantic is getting more subscribers from Google",
)


def test_the_track_is_capped():
    rows = [essay(title, hours=i + 1, source=f"Outlet {i}")
            for i, title in enumerate(DISTINCT)]
    assert len(select_analysis(rows, lambda s: False, now=NOW)) == MAX_ANALYSIS


def test_one_outlet_cannot_take_both_slots():
    rows = [essay(title, hours=i + 1) for i, title in enumerate(DISTINCT)]
    got = select_analysis(rows, lambda s: False, now=NOW)
    assert len({c.leader.source for c in got}) == len(got)


def test_mechanics_do_not_enter_the_news_selection():
    news = Story(title="YouTube launches creator fund partnership",
                 url="https://e.com/n", source="Tubefilter",
                 published_at=NOW - dt.timedelta(hours=2),
                 summary="creator brand deal launch", tier="trade")
    rows = [news, essay("How publishers grow subscription revenue")]
    got = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10)
    assert [c.leader.source for c in got] == ["Tubefilter"]


def test_an_empty_mechanics_track_renders_nothing():
    doc = compose([], None, mode="briefing", slot=None, now=NOW, mechanics=[])
    _, text, markup = render(doc)
    assert "How it works" not in markup
    assert "HOW IT WORKS" not in text


def test_mechanics_render_unnumbered_so_they_read_as_a_companion():
    mech = select_analysis([essay("How publishers grow subscription revenue")],
                           lambda s: False, now=NOW)
    doc = compose([], None, mode="briefing", slot=None, now=NOW, mechanics=mech)
    _, text, markup = render(doc)
    assert "How it works" in markup
    # Never an <h2>: that is what assert_render_shape counts as a story.
    assert "<h2" not in markup
    assert "1. How publishers" not in text


# --- the brief is still empty when there is no news ---

def test_mechanics_alone_do_not_make_a_brief_worth_sending(tmp_path, monkeypatch):
    """Rule 2. A day with no creator-business news is a quiet day, and an
    essay from last Tuesday is not a reason to send anyway."""
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", lambda now=None: ([], []))
    monkeypatch.setattr(app, "analysis_stories",
                        lambda now=None: ([essay("How publishers grow revenue")], []))
    result = app.run("briefing", tmp_path / "d.json", tmp_path / "s.json", dry_run=True)
    assert result["stories_selected"] == 0
    assert result["sent"] is False
