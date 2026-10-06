"""Cross-outlet dedup for company stories.

On 6 October Lauren received one acquisition twice, as two numbered stories:
NetInfluencer's "THE-TEAM Acquires UK Creator Agency Outreach Talent Group"
and The Hollywood Reporter's "The Team Buys U.K. Creators Agency In Expansion
Move". Duplicate stories are a named rejection pattern in her preference brief.

Two causes. Anchors came from a 76-name hardcoded vocabulary, and the
capitalised-word fallback was switched off for Title Case headlines, which is
what trade copy is -- so an acquisition involving companies not on the list
produced NO anchors at all. And the one path that merges differently-worded
headlines required a numeric anchor, so an acquisition with no price quoted
could never use it.
"""
from __future__ import annotations

from lozatron.cluster import (
    MERGE_BY_ACTION, MERGE_BY_TOPIC, action_class, build_clusters, canon_tokens,
    should_merge, similarity,
)
from lozatron.core import Story

import datetime as dt

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def merges(a: str, b: str) -> bool:
    ta, aa = canon_tokens(a)
    tb, ab = canon_tokens(b)
    return should_merge(similarity(ta, tb), aa, ab,
                        action_class(a), action_class(b), ta, tb)


# --- anchors no longer need a vocabulary ---

def test_a_title_case_headline_yields_anchors():
    """The regression that shipped the duplicate: zero anchors."""
    _, anchors = canon_tokens("THE·TEAM Acquires UK Creator Agency Outreach Talent Group")
    assert anchors, "a Title Case trade headline must produce proper-noun anchors"
    assert "outreach" in anchors


def test_common_headline_verbs_are_not_mistaken_for_names():
    _, anchors = canon_tokens("Spotify Acquires Podcast Studio Wondery")
    assert "acquir" not in anchors and "studio" not in anchors
    assert "wondery" in anchors


def test_dotted_abbreviations_match_undotted_ones():
    """One outlet writes U.K., another writes UK."""
    _, dotted = canon_tokens("Agency Buys U.K. Shop")
    _, plain = canon_tokens("Agency Buys UK Shop")
    assert "uk" in dotted and "uk" in plain


# --- the duplicate that shipped ---

def test_the_six_october_duplicate_now_merges():
    assert merges("THE·TEAM Acquires UK Creator Agency Outreach Talent Group",
                  "The Team Buys U.K. Creators Agency In Expansion Move")


def test_acquires_and_buys_are_the_same_action():
    assert action_class("Agency Acquires Shop") == action_class("Agency Buys Shop")
    assert action_class("Studio Launches Division") == "launch"
    assert action_class("A quiet week in creator news") is None


def test_a_commentary_headline_merges_with_the_announcement_it_discusses():
    assert merges(
        "TikTok Expands Off-Platform Ad Network to U.S. Advertisers, "
        "Adds AI Campaign Tools Through Claude, Perplexity",
        "TikTok’s ad network bet: More reach, more budget")


# --- and must not merge two events about one company ---

def test_two_acquisitions_by_one_buyer_stay_separate():
    """The guard that makes the action path safe. These share the buyer and
    the action class and nothing else, which is the signature of two events."""
    assert not merges("Spotify Acquires Podcast Studio Wondery",
                      "Spotify Buys Audio Network Megaphone")
    assert not merges("Netflix Acquires Game Studio Night School",
                      "Netflix Buys Animation House Scanline")


def test_two_launches_by_one_creator_stay_separate():
    assert not merges("MrBeast Launches Feastables In UK",
                      "MrBeast Launches Beast Games Season Two")


def test_different_companies_doing_the_same_thing_never_merge():
    """The original protection, still intact: high word overlap, no shared name."""
    assert not merges("YouTube announces creator monetization change",
                      "TikTok announces creator monetization change")


def test_same_company_different_subject_stays_separate():
    assert not merges("TikTok Expands Ad Network to U.S.",
                      "TikTok Tests New Creator Fund In Brazil")


# --- end to end through build_clusters ---

def story(title, source, hours=2):
    return Story(title=title, url=f"https://{source}.com/{abs(hash(title)) % 10**8}",
                 source=source, published_at=NOW - dt.timedelta(hours=hours),
                 summary="creator agency acquisition deal", tier="trade")


def test_the_duplicate_collapses_to_one_corroborated_entry():
    rows = [story("THE·TEAM Acquires UK Creator Agency Outreach Talent Group", "NetInfluencer"),
            story("The Team Buys U.K. Creators Agency In Expansion Move", "Hollywood Reporter", 3)]
    clusters = build_clusters(rows)
    assert len(clusters) == 1
    assert clusters[0].corroboration == 2


def test_merge_order_does_not_change_the_result():
    titles = [("THE·TEAM Acquires UK Creator Agency Outreach Talent Group", "NetInfluencer"),
              ("The Team Buys U.K. Creators Agency In Expansion Move", "Hollywood Reporter"),
              ("Spotify Acquires Podcast Studio Wondery", "Variety"),
              ("MrBeast Launches Beast Games Season Two", "Tubefilter")]
    import random
    sizes = set()
    for seed in range(20):
        rows = [story(t, s, hours=i + 1) for i, (t, s) in enumerate(titles)]
        random.Random(seed).shuffle(rows)
        sizes.add(tuple(sorted(len(c.members) for c in build_clusters(rows))))
    assert len(sizes) == 1, f"clustering is order dependent: {sizes}"


def test_the_thresholds_are_ordered_as_documented():
    assert MERGE_BY_TOPIC == MERGE_BY_ACTION
