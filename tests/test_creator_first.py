"""Creator-first scoring: creators doing business, not platforms shipping features.

Lauren's verdict on the 6 October edition, relayed verbatim: "thank you! it's
definitely less creator first. can you tell it to provide more creator
business-related news?"

The brief that morning led on a TikTok ad network and an Instagram API change.
Both are real creator-economy news and neither is a creator. Nothing in the
scoring could tell the difference, because STRONG_TERMS matches the vocabulary
of the beat ("creator", "monetization", "brand deal") and a platform story
uses that vocabulary as fluently as a creator story does.

So the bonus is about the *subject* of the headline, not its topic. A named
creator doing something commercial outranks a platform doing something to
creators. The guards matter as much as the bonus: NOT_A_CREATOR exists because
"TikTok's ad network" is a possessive proper noun and would otherwise score as
a person, which is the precise failure this is meant to fix.
"""
from __future__ import annotations

import datetime as dt

from lozatron.core import (
    creator_first_bonus,
    creator_subject,
    relevance,
)
from lozatron.core import Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 13, 0, tzinfo=UTC)


def story(title: str, summary: str = "A business story about the creator economy.") -> Story:
    return Story(
        title=title,
        url="https://example.com/1",
        source="Tubefilter",
        published_at=NOW - dt.timedelta(hours=3),
        summary=summary,
        score=0,
        tier="trade",
    )


# -- the subject test ------------------------------------------------------

def test_a_named_creator_in_a_role_is_the_subject():
    assert creator_subject("YouTuber Dhar Mann expands his studio")
    assert creator_subject("Influencer Matilda Djerf takes outside investment")


def test_a_possessive_person_is_the_subject():
    assert creator_subject("Dhar Mann's studio takes on scripted work")
    assert creator_subject("MrBeast's holding company buys a production arm")


def test_a_platform_possessive_is_not_a_creator():
    """The whole point. These are the headlines that crowded her brief out."""
    assert not creator_subject("TikTok's ad network opens to all advertisers")
    assert not creator_subject("Instagram's API change breaks scheduling tools")
    assert not creator_subject("YouTube's monetization update lands next month")
    assert not creator_subject("Spotify's podcast payouts shift again")


def test_a_trade_outlet_possessive_is_not_a_creator():
    """"Digiday's report on creator rates" is a citation, not a subject."""
    assert not creator_subject("Digiday's report on creator rates")
    assert not creator_subject("Adweek's survey of influencer pricing")


def test_a_bare_topic_headline_has_no_subject():
    assert not creator_subject("Creator economy funding slows in Q3")


# -- the bonus ------------------------------------------------------------

def test_a_creator_subject_outscores_a_platform_story():
    creator = creator_first_bonus(story("YouTuber Dhar Mann raises $20M for his studio"))
    platform = creator_first_bonus(story("TikTok's ad network opens to all advertisers"))
    assert creator > platform
    assert platform == 0


def test_money_in_the_headline_earns_its_point():
    """Her brief asks for numbers. A dated amount is a business event."""
    assert creator_first_bonus(story("Influencer Jane Doe raises $12M")) > \
        creator_first_bonus(story("Influencer Jane Doe raises a round"))


def test_deal_economics_score_where_strong_terms_are_silent():
    """CPMs and take rates are how creator money works. No STRONG_TERM covers them."""
    assert creator_first_bonus(story("Creator CPM rates fall 14% year on year")) > 0
    assert creator_first_bonus(story("Brands cut influencer fees as take rate climbs")) > 0


def test_formation_counts_only_with_a_creator_present():
    """Otherwise "launches" matches every product announcement on the wire."""
    with_creator = creator_first_bonus(story("Creator trio launches a production company"))
    without = creator_first_bonus(story("Oracle launches a new analytics suite"))
    assert with_creator > 0
    assert without == 0


def test_the_bonus_never_goes_negative_or_unbounded():
    for title in ("", "A", "Creator " * 40, "TikTok's TikTok's TikTok's"):
        assert 0 <= creator_first_bonus(story(title)) <= 7


# -- it actually reaches the ranking -------------------------------------

def test_the_bonus_is_wired_into_relevance():
    """Measured on the live pool: Dhar Mann scored 3 and sat below the floor.

    A bonus that is computed and never consulted is the failure mode this
    whole build keeps hitting, so this asserts the wiring, not the function.
    """
    creator = story("YouTuber Dhar Mann raises $20M for his studio")
    platform = story("TikTok's ad network opens to all advertisers")
    assert relevance(creator) > relevance(platform)
