"""Her voice rules, as code.

The preference brief's own top lesson: "Taste must become gates. Freshness,
deduplication, evidence, scope, depth and source diversity work best as
deterministic checks, not prompt reminders."

These are the voice half. The split is deliberate and honest about what code
can do. A dash is mechanical, so it is a gate. A warm-up sentence sitting in
front of the point is removable, so it is removed. A warm-up with the point
embedded in it ("It illustrates how X") can only be fixed by rewriting, which
code cannot do without mangling the sentence, so that one is stated in the
model's instructions instead and not pretended away here.
"""
from __future__ import annotations

from lozatron.analyst import SYSTEM, buzzwords_in, rejected, strip_warmup


# --- no em or en dashes. a flat refusal ---

def test_an_em_dash_is_rejected():
    assert rejected("five NYFW partnerships—two shows, a masterclass")


def test_an_en_dash_is_rejected():
    assert rejected("rates run 20–60% above platform average")


def test_ordinary_punctuation_passes():
    assert not rejected("five NYFW partnerships: two shows and a masterclass")
    assert not rejected("a scheduling-constrained production input")


def test_the_rule_is_in_the_model_instructions_too():
    """Belt and braces, as with formatting: say it, then enforce it."""
    assert "em dash" in SYSTEM.lower()
    assert "lead with the point" in SYSTEM.lower()


# --- lead with the point ---

def test_a_standalone_warmup_sentence_is_removed():
    assert strip_warmup("This is notable. It shows how creators consolidate.") == (
        "It shows how creators consolidate.")


def test_stacked_warmups_are_removed():
    got = strip_warmup("This is notable. Importantly, creators are consolidating.")
    assert got == "Creators are consolidating."


def test_a_lead_in_label_is_removed():
    assert strip_warmup("The relevant insight is that renting is the growth segment.") == (
        "Renting is the growth segment.")


def test_an_embedded_warmup_is_unwrapped_to_the_clause():
    """The 3 October opener. The point is inside the throat-clear, so the
    prefix comes off and the clause it was wrapping is promoted."""
    assert strip_warmup(
        "It illustrates how narrow projects function as content capital.") == (
        "Narrow projects function as content capital.")


def test_a_colon_opener_is_unwrapped():
    assert strip_warmup(
        "This is the maturation pattern worth tracking: a gaming creator "
        "moving to owned businesses.") == (
        "A gaming creator moving to owned businesses.")


def test_a_prefix_with_no_hinge_is_left_alone():
    """"This is a financier, not a studio arm" has no `that`, `how` or colon
    to cut at, so stripping it would leave a fragment."""
    for text in ("This is a financier, not a studio development arm, treating "
                 "creator IP as a fundable asset class.",
                 "It is a rare clean growth datapoint for a digital publisher."):
        assert strip_warmup(text) == text


def test_a_field_is_never_reduced_to_a_fragment():
    assert strip_warmup("It shows how.") == "It shows how."


def test_clean_prose_is_untouched():
    for text in ("Likeness licensing turns a creator into parallelizable inventory.",
                 "Renting is now the growth segment for home creators.",
                 "AGC is a financier treating creator IP as a fundable asset."):
        assert strip_warmup(text) == text


def test_stripping_restores_the_capital():
    got = strip_warmup("This is notable. creators are consolidating quickly now.")
    assert got.startswith("C")


# --- buzzwords are reported, not binned ---

def test_buzzwords_are_detected():
    found = buzzwords_in("We will leverage our synergy to supercharge growth")
    assert "synergy" in found and "supercharge" in found


def test_a_term_of_art_is_not_a_buzzword():
    """Binning a good analysis over one word would be worse than the word,
    so this reports and does not reject."""
    assert buzzwords_in("ad revenue share and rights retention change the economics") == []
    assert not rejected("ad revenue share and rights retention change the economics")


# --- rejection patterns her brief names ---

def test_the_event_calendar_that_shipped_is_now_rejected():
    """Delivered on 21 September: "Creator Economy Event Calendar - September
    21, 2026 - VidSummit". Her brief rejects routine event calendars by name,
    and a calendar recurs forever and dates instantly."""
    import datetime as dt
    from lozatron.core import Story, passes_hard_gates
    now = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
    item = Story(title="Creator Economy Event Calendar – October 2026 – VidSummit",
                 url="https://e.com/x", source="NetInfluencer",
                 published_at=now - dt.timedelta(hours=2), summary="creator deal")
    assert not passes_hard_gates(item, now, 48)


def test_jobs_calendars_and_generic_explainers_are_rejected():
    from lozatron.core import JUNK_PHRASES
    for title in ("We're hiring a creator manager",
                  "Upcoming events for creators this quarter",
                  "Register now for the creator webinar",
                  "The ultimate guide to influencer marketing",
                  "Everything you need to know about TikTok Shop"):
        assert any(p in title.lower() for p in JUNK_PHRASES), title


def test_a_creator_opening_a_hotel_is_still_creator_business():
    """The enterprise-hospitality terms are kept narrow for exactly this.
    `eligible` already requires a creator term in the headline, so a bare
    "hotel" would buy almost nothing and cost a real story."""
    from lozatron.core import JUNK_PHRASES
    assert not any(p in "MrBeast Opens Boutique Hotel Brand".lower()
                   for p in JUNK_PHRASES)


def test_the_titles_that_actually_shipped_all_survive():
    from lozatron.core import JUNK_PHRASES
    for title in ("THE·TEAM Acquires UK Creator Agency Outreach Talent Group",
                  "AGC Studios Launches Creator Division to Turn Digital IP Into Film",
                  "Fortnite Player Cody 'Clix' Conrod Launches Creator Holding Company",
                  "Live Nation is capitalizing on the creator economy with a new firm",
                  "Arrowfly Expands Hospitality Reach With Prosper Company Acquisition"):
        assert not any(p in title.lower() for p in JUNK_PHRASES), title
