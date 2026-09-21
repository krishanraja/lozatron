"""Clustering correctness.

Two failure modes matter more than the happy path. A false merge presents two
different events as one story badged with an inflated source count -- a
correctness failure that reads like a feature. A transitive chain-merge
collapses unrelated events into one blob. Both are tested explicitly.
"""

import datetime as dt
import random

from lozatron.cluster import (
    Cluster,
    build_clusters,
    canon_tokens,
    should_merge,
    similarity,
)
from lozatron.core import Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def story(title, source="Test", url=None, minutes=0):
    # The summary carries the creator-business context the mandate gates in
    # core.eligible() require; clustering itself reads only the title.
    return Story(
        title=title,
        url=url or f"https://{source.lower().replace(' ', '')}.com/{abs(hash(title)) % 10000}",
        source=source,
        published_at=NOW - dt.timedelta(minutes=minutes),
        summary="Creator economy business update on a partnership deal",
    )


def merged(a: str, b: str) -> bool:
    ta, aa = canon_tokens(a)
    tb, ab = canon_tokens(b)
    return should_merge(similarity(ta, tb), aa, ab)


# --- canonicalisation ---

def test_publisher_tail_is_stripped():
    tokens, _ = canon_tokens("MrBeast signs deal — Deadline")
    assert "deadline" not in tokens


def test_domain_noise_is_dropped():
    tokens, _ = canon_tokens("Creator economy news: Patreon launches storefront")
    assert not {"creator", "economy", "news", "launch"} & tokens
    assert "patreon" in tokens


def test_known_entities_are_anchors_regardless_of_case():
    _, anchors = canon_tokens("youtube expands payouts")
    assert "youtube" in anchors


def test_money_amounts_are_anchors():
    # The currency symbol is not a word character, so the token is the amount.
    _, anchors = canon_tokens("Substack raises $65M in new round")
    assert "65m" in anchors


def test_inflection_is_stemmed_consistently():
    signs, _ = canon_tokens("Patreon signs deal")
    signed, _ = canon_tokens("Patreon signed deal")
    assert signs == signed


# --- the merges that must happen ---

def test_same_wire_story_across_outlets_merges():
    assert merged(
        "MrBeast signs landmark deal with Amazon",
        "MrBeast Signs Landmark Deal With Amazon Prime — Deadline",
    )


def test_headline_rewording_of_one_event_merges():
    assert merged(
        "Patreon acquires podcast startup Moment",
        "Patreon Acquires Moment, A Podcast Startup | Variety",
    )


# --- the merges that must NOT happen ---

def test_same_action_by_different_platforms_does_not_merge():
    """The anchor gate's whole reason for existing."""
    assert not merged(
        "YouTube announces creator monetization change",
        "TikTok announces creator monetization change",
    )


def test_same_platform_different_events_do_not_merge():
    assert not merged(
        "YouTube launches new shopping storefront for creators",
        "YouTube appoints new chief marketing officer",
    )


def test_different_funding_amounts_do_not_merge():
    assert not merged(
        "Beehiiv raises $12M Series A",
        "Substack raises $65M Series B",
    )


# --- assignment ---

def test_clustering_is_order_independent():
    rows = [
        story("MrBeast signs landmark deal with Amazon", "Variety"),
        story("MrBeast Signs Landmark Deal With Amazon Prime", "Deadline"),
        story("TikTok appoints new head of creator partnerships", "Digiday"),
        story("Substack raises $65M Series B", "TechCrunch"),
    ]
    baseline = None
    rng = random.Random(1234)
    for _ in range(20):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        shape = sorted(
            (cluster.key, tuple(sorted(cluster.outlets)))
            for cluster in build_clusters(shuffled)
        )
        baseline = baseline or shape
        assert shape == baseline, "cluster membership must not depend on input order"


def test_no_transitive_chain_merge():
    """A resembles B and B resembles C, but A is unlike C.

    Comparing against cluster leaders only keeps these apart; comparing against
    every member would collapse all three into one entry.
    """
    rows = [
        story("Patreon launches storefront for creators", "A", minutes=30),
        story("Patreon launches storefront and membership tiers", "B", minutes=20),
        story("Patreon membership tiers reach one million subscribers", "C", minutes=10),
    ]
    clusters = build_clusters(rows)
    assert len(clusters) >= 2, "unrelated ends of a chain must not share a cluster"


def test_cluster_reports_every_outlet_and_corroboration():
    rows = [
        story("MrBeast signs landmark deal with Amazon", "Variety", minutes=40),
        story("MrBeast Signs Landmark Deal With Amazon Prime", "Deadline", minutes=20),
    ]
    clusters = build_clusters(rows)
    assert len(clusters) == 1
    assert sorted(clusters[0].outlets) == ["Deadline", "Variety"]
    assert clusters[0].corroboration == 2


def test_leader_is_the_earliest_member():
    early = story("Patreon acquires podcast startup Moment", "Variety", minutes=60)
    late = story("Patreon Acquires Moment, A Podcast Startup", "Deadline", minutes=5)
    clusters = build_clusters([late, early])
    assert clusters[0].leader.source == "Variety"


def test_member_keys_cover_every_member():
    rows = [
        story("MrBeast signs landmark deal with Amazon", "Variety", minutes=40),
        story("MrBeast Signs Landmark Deal With Amazon Prime", "Deadline", minutes=20),
    ]
    cluster = build_clusters(rows)[0]
    assert set(cluster.member_keys()) == {row.key for row in rows}


def test_unrelated_stories_stay_separate():
    rows = [
        story("Spotify expands podcast payouts", "Podnews"),
        story("Roblox signs brand partnership with Nike", "Verge"),
        story("Substack raises $65M Series B", "TechCrunch"),
    ]
    assert len(build_clusters(rows)) == 3


def test_empty_input_yields_no_clusters():
    assert build_clusters([]) == []


def test_cluster_key_is_stable_for_the_same_leader():
    rows = [story("Patreon acquires podcast startup Moment", "Variety")]
    assert build_clusters(rows)[0].key == build_clusters(rows)[0].key


# --- selection ---

def test_select_clusters_suppresses_an_event_seen_under_any_member():
    """Outlet B's copy must not ship because outlet A's already did.

    This is the cross-outlet duplicate closing without a new ledger concept.
    """
    seen = story("MrBeast signs landmark deal with Amazon", "Variety", minutes=40)
    other = story("MrBeast Signs Landmark Deal With Amazon Prime", "Deadline", minutes=20)
    from lozatron.cluster import select_clusters

    chosen = select_clusters(
        [seen, other], lambda s: s.key == seen.key,
        now=NOW, window_hours=48, limit=10,
    )
    assert chosen == []


def test_select_clusters_ranks_and_limits():
    from lozatron.cluster import select_clusters

    rows = [
        story("Patreon signs brand partnership deal", "A", minutes=30),
        story("Spotify launches creator payout program", "B", minutes=90),
        story("Roblox acquires marketplace startup", "C", minutes=45),
    ]
    chosen = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=2)
    assert len(chosen) == 2
    assert chosen[0].score >= chosen[1].score


def test_select_clusters_applies_the_eligibility_gates():
    from lozatron.cluster import select_clusters

    junk = story("Top 10 influencer marketing platforms", "A", minutes=10)
    chosen = select_clusters([junk], lambda s: False, now=NOW, window_hours=48, limit=10)
    assert chosen == []


# --- Lauren's sourcing rules, encoded ---

def tiered(title, tier, source="Test", minutes=0):
    row = story(title, source, minutes=minutes)
    row.tier = tier
    return row


def test_reddit_only_story_is_never_delivered():
    """'Reddit is signal, not proof. Confirm before including.'"""
    from lozatron.cluster import select_clusters
    rows = [tiered("Creator signs brand partnership deal", "community", "r/creators")]
    assert select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10) == []


def test_reddit_story_ships_once_a_real_source_corroborates_it():
    from lozatron.cluster import select_clusters
    rows = [
        tiered("Patreon acquires podcast startup Moment", "community", "r/creators", minutes=30),
        tiered("Patreon Acquires Moment, A Podcast Startup", "trade", "Variety", minutes=20),
    ]
    chosen = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10)
    assert len(chosen) == 1
    assert chosen[0].confirmed


def test_primary_source_outranks_trade_on_the_same_event():
    """A creator's own announcement beats a trade write-up of it."""
    from lozatron.cluster import select_clusters
    rows = [
        tiered("Spotify signs exclusive creator podcast deal", "trade", "Variety", minutes=10),
        tiered("Patreon signs landmark brand partnership", "primary", "MrBeast", minutes=10),
    ]
    chosen = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10)
    assert chosen[0].primary, "the primary signal must sort first"


def test_no_outlet_takes_more_than_two_slots():
    """The reported email was three NetInfluencer stories in a row."""
    from lozatron.cluster import select_clusters
    # Distinct events from one outlet. Near-identical titles would legitimately
    # cluster into a single entry, which is a different mechanism.
    titles = [
        "Patreon acquires podcast startup Moment",
        "Spotify launches brand partnership programme",
        "Roblox signs licensing deal with Nike",
        "Substack raises funding from investors",
        "Twitch appoints new chief revenue officer",
    ]
    rows = [tiered(t, "trade", "NetInfluencer", minutes=n) for n, t in enumerate(titles, 1)]
    chosen = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10)
    assert len(chosen) == 2


def test_cap_removes_the_surplus_rather_than_reordering():
    from lozatron.cluster import cap_per_source

    class Fake:
        def __init__(self, src, score):
            self.leader = type("S", (), {"source": src})()
            self.score = score

    rows = [Fake("A", 9), Fake("A", 8), Fake("B", 7), Fake("A", 6), Fake("C", 5)]
    kept = cap_per_source(rows, limit=10)
    assert [c.leader.source for c in kept] == ["A", "A", "B", "C"]


def test_mixed_tier_cluster_counts_as_confirmed():
    from lozatron.cluster import build_clusters
    rows = [
        tiered("MrBeast signs landmark deal with Amazon", "community", "r/youtube", minutes=40),
        tiered("MrBeast Signs Landmark Deal With Amazon Prime", "trade", "Variety", minutes=20),
    ]
    cluster = build_clusters(rows)[0]
    assert cluster.confirmed
    assert cluster.tiers == {"community", "trade"}


# --- money written two ways is one amount ---

def test_the_same_amount_written_two_ways_normalises():
    from lozatron.cluster import normalise_money
    for text in ("$400 Million", "$400M", "400 million", "$400m"):
        assert "400m" in normalise_money(text).lower().replace(" ", ""), text


def test_billions_and_decimals_normalise():
    from lozatron.cluster import normalise_money
    assert "1.2b" in normalise_money("a $1.2 billion round").replace(" ", "")
    assert "275m" in normalise_money("closes a $275 Million deal").replace(" ", "")


def test_two_outlets_on_one_ruling_merge_despite_different_wording():
    """The reported miss: these shipped as two separate stories."""
    assert merged(
        "Judge Casts Doubt on TikTok's $400 Million Privacy Deal, Threatens to Keep Court Oversight Alive",
        "U.S. Judge Signals Rejection of Key Piece in TikTok's $400M Privacy Settlement",
    )


def test_anchor_merge_needs_both_a_name_and_a_figure():
    """One shared company alone is not enough to call it the same event."""
    assert not merged(
        "TikTok launches a new shopping storefront for creators",
        "TikTok appoints a new head of creator partnerships",
    )


def test_different_companies_with_different_amounts_stay_apart():
    assert not merged(
        "Beehiiv raises $12M Series A funding round",
        "Substack raises $65M Series B funding round",
    )


def test_same_company_different_amounts_stay_apart():
    assert not merged(
        "Patreon closes a $50 million funding round",
        "Patreon closes a $220 million acquisition",
    )
