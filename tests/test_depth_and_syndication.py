"""Depth, and a republisher is not a second outlet.

Both from the 6 October edition, checked against her preference brief.

It carried four stories, three with the full what happened / why it matters /
what to watch treatment and one as a bare feed blurb. Her brief: depth is part
of the product, one-line summaries are too thin, and fewer strong stories beat
a list containing weak ones.

It also attributed a story to "Digiday and Biztoc.com", which reads as two
outlets confirming each other. Biztoc republishes Digiday. Her brief rejects
"old news presented as today, especially when a secondary outlet was not the
original source" and "over-reliance on search aggregators".
"""
from __future__ import annotations

import datetime as dt

from lozatron.brief import Brief, Entry
from lozatron.cluster import build_clusters, select_clusters
from lozatron.core import Story, is_aggregator

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 13, 0, tzinfo=UTC)


def entry(title, analysed: bool):
    return Entry(title=title, url=f"https://e.com/{abs(hash(title)) % 10**6}",
                 outlets=["Tubefilter"], corroboration=1, age_hours=3,
                 summary="A feed blurb.",
                 what_happened="It happened." if analysed else "",
                 why_it_matters="It matters." if analysed else "",
                 what_to_watch="Watch." if analysed else "",
                 analysed=analysed)


def brief(entries):
    return Brief(mode="briefing", slot=None, generated_at=NOW, entries=entries)


# --- depth ---

def test_an_unanalysed_story_is_dropped_beside_analysed_ones():
    doc = brief([entry("Covered one", True), entry("Bare blurb", False),
                 entry("Covered two", True)])
    dropped = doc.drop_thin_entries()
    assert [e.title for e in dropped] == ["Bare blurb"]
    assert [e.title for e in doc.entries] == ["Covered one", "Covered two"]


def test_a_wholly_unanalysed_edition_is_left_alone():
    """If the analyst failed for everything, every story is thin together and
    the deterministic brief is the honest fallback, flagged as degraded."""
    doc = brief([entry("One", False), entry("Two", False)])
    assert doc.drop_thin_entries() == []
    assert len(doc.entries) == 2


def test_an_edition_with_nothing_thin_is_unchanged():
    doc = brief([entry("One", True), entry("Two", True)])
    assert doc.drop_thin_entries() == []
    assert len(doc.entries) == 2


# --- syndication ---

def test_known_republishers_are_recognised():
    assert is_aggregator("Biztoc.com", "http://digiday.com/x")
    assert is_aggregator("Yahoo Entertainment", "https://news.yahoo.com/x")
    assert is_aggregator("", "https://www.msn.com/en-us/x")


def test_real_outlets_are_not():
    for name, url in [("Digiday", "https://digiday.com/x"),
                      ("Tubefilter", "https://www.tubefilter.com/x"),
                      ("NetInfluencer", "https://www.netinfluencer.com/x")]:
        assert not is_aggregator(name, url), name


def story(title, source, tier="trade", hours=3):
    return Story(title=title, url=f"https://{source.lower()}.com/{abs(hash(title)) % 10**6}",
                 source=source, published_at=NOW - dt.timedelta(hours=hours),
                 summary="creator agency fees deal transparency", tier=tier)


def test_a_republished_copy_does_not_count_as_corroboration():
    title = "Agency fees in creator deals are the next transparency headache"
    rows = [story(title, "Digiday"), story(title, "Biztoc.com", "syndicated", 2)]
    clusters = build_clusters(rows)
    assert len(clusters) == 1
    assert clusters[0].corroboration == 1
    assert clusters[0].outlets == ["Digiday"]


def test_a_republished_copy_never_leads_the_entry():
    """Even when its timestamp is earlier, which is what picks the leader."""
    title = "Agency fees in creator deals are the next transparency headache"
    rows = [story(title, "Biztoc.com", "syndicated", hours=9),
            story(title, "Digiday", hours=3)]
    clusters = build_clusters(rows)
    assert clusters[0].leader.source == "Digiday"


def test_a_syndicated_only_cluster_is_not_deliverable():
    lone = story("Creator agency fees deal transparency", "Biztoc.com", "syndicated")
    assert select_clusters([lone], lambda s: False, now=NOW,
                           window_hours=48, limit=10) == []


def test_a_syndicated_only_cluster_still_names_a_source_if_rendered():
    """It cannot ship, but it must never render with no attribution at all."""
    lone = story("Creator agency fees", "Biztoc.com", "syndicated")
    assert build_clusters([lone])[0].outlets == ["Biztoc.com"]
