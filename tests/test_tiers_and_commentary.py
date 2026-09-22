"""Tiers, and the rule that a social post is never a story.

Two defects this locks shut:

  Nothing in production ever set a tier, so every paid story arrived as
  "trade". `Cluster.confirmed` rejects a cluster whose members are all
  community -- the guard written to stop uncorroborated Reddit reaching
  Lauren -- and it was therefore protecting nothing. The flood mechanism,
  dormant behind a feature flag.

  `_as_story` put post text in the title field, which is how a Tinx
  "#TidePartner" ad shipped as a breaking creator-business update.
"""
from __future__ import annotations

import datetime as dt

from lozatron.apify import PROFILE_TIERS, _as_story
from lozatron.cluster import (
    PATTERN_MIN_ACCOUNTS, attach_commentary, find_patterns, select_clusters,
)
from lozatron.core import Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def story(title, *, tier="trade", source="Test", summary="", hours=2, url=None):
    return Story(
        title=title, url=url or f"https://example.com/{abs(hash(title)) % 10**8}",
        source=source, published_at=NOW - dt.timedelta(hours=hours),
        summary=summary or "creator brand deal launch partnership", tier=tier,
    )


# --- tiers are real ---

def test_paid_profiles_carry_their_real_tier():
    assert PROFILE_TIERS["reddit_creator"] == "community"
    assert PROFILE_TIERS["x_creator"] == "social"
    assert PROFILE_TIERS["youtube_community"] == "social"


def test_a_reddit_row_is_tagged_community_not_trade():
    row = {"title": "Cant monetize my videos", "url": "https://reddit.com/r/x/1",
           "createdAt": "2026-09-22T10:00:00Z", "body": "help"}
    item = _as_story("reddit_creator", row)
    assert item is not None and item.tier == "community"


def test_an_unknown_profile_fails_closed_to_community():
    """A new actor must not be able to reach the brief by being unlisted."""
    row = {"title": "Something", "url": "https://x.com/1", "createdAt": "2026-09-22T10:00:00Z"}
    assert _as_story("some_new_actor", row).tier == "community"


def test_a_social_row_does_not_use_post_text_as_a_headline():
    row = {"text": "Making your first time simple \U0001f9fc #TidePartner",
           "url": "https://x.com/tinx/1", "createdAt": "2026-09-22T10:00:00Z",
           "author": "tinx"}
    item = _as_story("x_creator", row)
    assert item is not None
    assert item.tier == "social"
    assert item.title == "tinx"
    assert "TidePartner" in item.summary


# --- the flood guard, now that it guards something ---

def test_a_community_only_cluster_is_not_deliverable():
    lone = story("Creator monetization brand deal question", tier="community",
                 source="Apify/reddit_creator")
    assert select_clusters([lone], lambda s: False, now=NOW, window_hours=48, limit=10) == []


def test_a_community_story_ships_once_a_trade_outlet_carries_it():
    title = "YouTube launches creator fund partnership"
    rows = [story(title, tier="community", source="Apify/reddit_creator"),
            story(title, tier="trade", source="Tubefilter")]
    got = select_clusters(rows, lambda s: False, now=NOW, window_hours=48, limit=10)
    assert len(got) == 1


# --- social is never a story ---

def test_a_social_post_never_becomes_a_numbered_entry():
    post = story("mrbeast", tier="social", source="Apify/x_creator",
                 summary="we just signed a huge brand partnership deal with a creator fund")
    assert select_clusters([post], lambda s: False, now=NOW, window_hours=48, limit=10) == []


def test_social_cannot_outrank_reporting():
    """The +3 primary bonus is gone; a post must not displace a trade story."""
    trade = story("YouTube launches creator fund partnership", tier="trade", source="Tubefilter")
    post = story("mrbeast", tier="social", source="Apify/x_creator",
                 summary="youtube launches creator fund partnership")
    got = select_clusters([trade, post], lambda s: False, now=NOW, window_hours=48, limit=10)
    assert [c.leader.source for c in got] == ["Tubefilter"]


# --- commentary attaches, patterns aggregate ---

def test_commentary_attaches_to_the_story_it_discusses():
    trade = story("YouTube launches creator fund for Shorts monetization", source="Tubefilter")
    clusters = select_clusters([trade], lambda s: False, now=NOW, window_hours=48, limit=10)
    posts = [story(f"acct{i}", tier="social", url=f"https://x.com/{i}",
                   summary="youtube launches a creator fund for shorts monetization")
             for i in range(2)]
    found = attach_commentary(clusters, posts)
    assert found[clusters[0].key] == posts


def test_an_unrelated_post_does_not_attach():
    trade = story("YouTube launches creator fund for Shorts monetization", source="Tubefilter")
    clusters = select_clusters([trade], lambda s: False, now=NOW, window_hours=48, limit=10)
    off = [story("acct", tier="social", url="https://x.com/z",
                 summary="the weather in london is miserable again today")]
    assert attach_commentary(clusters, off) == {}


def test_a_pattern_needs_several_distinct_accounts():
    theme = "tiktok payout timing is broken for everyone right now"
    few = [story(f"acct{i}", tier="social", url=f"https://x.com/{i}", summary=theme)
           for i in range(PATTERN_MIN_ACCOUNTS - 1)]
    assert find_patterns(few, {}) == []
    many = [story(f"acct{i}", tier="social", url=f"https://x.com/{i}", summary=theme)
            for i in range(PATTERN_MIN_ACCOUNTS)]
    assert len(find_patterns(many, {})) == 1


def test_one_loud_account_is_not_a_pattern():
    """Six posts from one person is one person, not a trend."""
    theme = "tiktok payout timing is broken for everyone right now"
    spam = [story("sameguy", tier="social", url=f"https://x.com/sameguy/{i}", summary=theme)
            for i in range(6)]
    assert find_patterns(spam, {}) == []


def test_posts_already_attached_to_a_story_do_not_also_form_a_pattern():
    theme = "youtube launches a creator fund for shorts monetization"
    posts = [story(f"acct{i}", tier="social", url=f"https://x.com/{i}", summary=theme)
             for i in range(4)]
    trade = story("YouTube launches creator fund for Shorts monetization", source="Tubefilter")
    clusters = select_clusters([trade], lambda s: False, now=NOW, window_hours=48, limit=10)
    attached = attach_commentary(clusters, posts)
    assert find_patterns(posts, attached) == []
