"""Relevance, recency and the inflection defect.

Three defects the live measurement exposed, each of which silently shrank the
brief rather than failing visibly:

  1. The score floor applied to `rank`, which had recency inside it, so the
     declared 48-hour window was really eight hours.
  2. The word-boundary conversion narrowed bare stems, so "launches",
     "signs", "raises" and "sells" -- the commonest verbs in a
     creator-business headline -- stopped matching the business gate.
  3. Feed titles were escaped without being unescaped, so entities printed
     literally.
"""
from __future__ import annotations

import datetime as dt

from lozatron.core import (
    BUSINESS_TERMS, STRONG_TERMS, Story, clean_feed_text, eligible,
    freshness_tier, rank, recency_bonus, relevance, term_matcher,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def story(title, hours, summary="Creator economy business update"):
    return Story(
        title=title, url="https://example.com/a", source="Test",
        published_at=NOW - dt.timedelta(hours=hours), summary=summary,
    )


# --- relevance is independent of age ---

def test_relevance_does_not_move_with_age():
    title = "YouTube launches a creator fund"
    assert relevance(story(title, 1)) == relevance(story(title, 40))


def test_recency_still_orders_two_equally_relevant_stories():
    fresh = story("YouTube launches a creator fund", 1)
    old = story("YouTube launches a creator fund", 40)
    assert rank(fresh, NOW) > rank(old, NOW)
    assert recency_bonus(fresh, NOW) > recency_bonus(old, NOW)


def test_a_strong_story_clears_the_floor_at_forty_hours():
    """The bug: at 40h the old rank was 1, so the floor of 8 rejected it
    however relevant it was. The 48-hour window has to be a real window."""
    from lozatron.cluster import MIN_RELEVANCE
    assert relevance(story("MrBeast signs a licensing deal with YouTube", 40)) >= MIN_RELEVANCE


def test_a_weak_story_is_still_rejected_however_fresh():
    from lozatron.cluster import MIN_RELEVANCE
    weak = story("A creator reflects on the year", 1, summary="A creator reflects.")
    assert relevance(weak) < MIN_RELEVANCE


# --- freshness tiers, her stated rule ---

def test_freshness_tiers_match_the_stated_rule():
    assert freshness_tier(story("x", 2), NOW) == "priority"
    assert freshness_tier(story("x", 8), NOW) == "priority"
    assert freshness_tier(story("x", 20), NOW) == "secondary"
    assert freshness_tier(story("x", 40), NOW) == "fallback"
    assert freshness_tier(story("x", 60), NOW) == "stale"


# --- the inflection defect ---

def test_common_headline_verbs_match_the_business_gate():
    business = term_matcher(BUSINESS_TERMS)
    strong = term_matcher(STRONG_TERMS)
    for phrase in ("launches a fund", "launching a storefront", "signs with WME",
                   "raises $40M", "sells its stake", "appoints a president",
                   "acquires the studio", "inks a deal", "hiring a CEO"):
        assert business.search(phrase), phrase
        assert strong.search(phrase), phrase


def test_the_prefix_stems_do_not_admit_their_short_false_friends():
    """`sign*` would match signal, signage and significant, so it is not used."""
    business = term_matcher(BUSINESS_TERMS)
    for phrase in ("a signal of intent", "clear signage", "significant growth",
                   "tourism board", "kickstand review", "Read My Book"):
        assert not business.search(phrase), phrase


def test_a_real_headline_that_used_to_be_rejected_now_passes():
    # Measured live on 2026-09-22: failed the business half because "launching"
    # did not match the bare stem "launch".
    item = story(
        "Kick creator N3on is reportedly launching a never-ending AI-driven livestream",
        4, summary="The Kick creator is launching a new format.",
    )
    assert eligible(item, NOW, 48)


# --- entity handling on titles ---

def test_feed_entities_are_resolved_in_titles():
    assert clean_feed_text("Alex Cooper&#8217;s Unwell Adds 4 Shows", 300) == (
        "Alex Cooper’s Unwell Adds 4 Shows"
    )
