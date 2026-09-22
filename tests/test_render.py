"""Rendering shape and escaping.

The September 2026 incident was a model emitting nested bold bullets that the
renderer reproduced faithfully. These tests feed deliberately hostile analysis
text through a full render and assert none of it survives as markup.
"""

import datetime as dt

import pytest

from lozatron.brief import Brief, Entry
from lozatron.render import assert_render_shape, render, render_html, subject

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 21, 13, 4, tzinfo=UTC)


def entry(**over):
    base = dict(
        title="Patreon acquires podcast startup Moment",
        url="https://variety.com/2026/patreon-moment",
        outlets=["Variety"], corroboration=1, age_hours=3,
        summary="A creator platform acquisition.",
        what_happened="Patreon acquired Moment for an undisclosed sum this morning.",
        why_it_matters="It moves Patreon into podcast distribution and away from memberships alone.",
        what_to_watch="Whether hosting is bundled into existing creator tiers.",
        analysed=True,
    )
    base.update(over)
    return Entry(**base)


def brief(entries=None, **over):
    base = dict(mode="briefing", slot="2026-09-21T09", generated_at=NOW,
                entries=entries if entries is not None else [entry()])
    base.update(over)
    return Brief(**base)


def test_renders_and_passes_its_own_shape_check():
    subj, text, markup = render(brief())
    assert subj == "Lozatron | Morning brief | Mon 21 Sep"
    assert "Patreon acquires podcast startup Moment" in text
    assert markup.startswith("<!doctype html>")


@pytest.mark.parametrize("hostile", [
    "<script>alert(1)</script> Patreon bought Moment for an undisclosed sum.",
    "**Patreon** bought Moment, a podcast startup, for an undisclosed sum.",
    "<h1>Patreon</h1> bought Moment, a podcast startup, for a sum.",
    '<img src=x onerror="alert(1)"> Patreon bought Moment for a sum.',
    "<iframe src='evil'></iframe> Patreon bought Moment for a sum today.",
])
def test_hostile_analysis_text_never_becomes_markup(hostile):
    markup = render_html(brief([entry(why_it_matters=hostile)]))
    # The parser is the authority: escaped text may still read as "onerror="
    # to a substring search while being inert inside a text node, so assert on
    # the parsed tag and attribute set rather than on the raw characters.
    assert_render_shape(markup, 1)
    assert "<script" not in markup
    assert "<iframe" not in markup
    assert "<h1>Patreon</h1>" not in markup
    # It survives as visible, escaped text rather than being silently dropped.
    assert "&lt;" in markup or "**" in markup


def test_hostile_title_is_escaped_in_the_link():
    markup = render_html(brief([entry(title='Patreon" onmouseover="alert(1)')]))
    assert 'onmouseover="alert(1)"' not in markup


def test_shape_check_rejects_a_heading_count_mismatch():
    """The specific September shape: stories collapsing into one block."""
    markup = render_html(brief([entry(), entry(title="Second story about creators")]))
    assert_render_shape(markup, 2)
    with pytest.raises(ValueError, match="story headings"):
        assert_render_shape(markup, 3)


def test_shape_check_rejects_disallowed_tags():
    with pytest.raises(ValueError, match="disallowed tags"):
        assert_render_shape("<html><body><h2>x</h2><script>1</script></body></html>", 1)


def test_shape_check_rejects_event_handlers():
    with pytest.raises(ValueError, match="event handler"):
        assert_render_shape('<html><body><h2 onclick="x()">t</h2></body></html>', 1)


def test_every_story_has_exactly_two_ways_to_open_it():
    """The headline link and the named read link. Two affordances, no more.

    A third copy of the URL would mean a duplicate story block rather than a
    deliberate second affordance, so the count is pinned rather than bounded.
    """
    rows = [entry(), entry(title="Second story about creator deals", url="https://deadline.com/b")]
    markup = render_html(brief(rows))
    for row in rows:
        assert markup.count(row.url) == 2


def test_links_announce_that_they_are_clickable():
    from lozatron.render import LINK
    markup = render_html(brief())
    assert markup.count("text-decoration:underline") >= 2
    assert LINK in markup
    assert "text-decoration:none" not in markup


def test_read_link_names_its_destination():
    markup = render_html(brief([entry(outlets=["Deadline"])]))
    assert "Read on Deadline" in markup


def test_masthead_carries_the_product_name_and_a_story_count():
    markup = render_html(brief([entry(), entry(title="Second creator deal story")]))
    assert "Lozatron" in markup
    assert "2 stories" in markup


def test_story_count_is_not_pluralised_at_one():
    assert "1 story" in render_html(brief([entry()]))


def test_unanalysed_entry_falls_back_to_the_summary():
    markup = render_html(brief([entry(analysed=False, summary="Raw feed text about a deal.")]))
    assert "Raw feed text about a deal." in markup
    assert "Why it matters" not in markup


def test_degraded_banner_names_the_reason():
    markup = render_html(brief(degraded="quota_exhausted"))
    assert "Analysis unavailable" in markup
    assert "quota_exhausted" in markup


def test_low_confidence_renders_as_a_flag_not_a_number():
    markup = render_html(brief([entry(low_confidence=True)]))
    assert "treat as unconfirmed" in markup
    assert "0.3" not in markup and "confidence: " not in markup


def test_corroboration_count_is_suppressed_when_outlets_are_named():
    markup = render_html(brief([entry(outlets=["Variety", "Deadline"], corroboration=2)]))
    assert "Variety and Deadline" in markup
    assert "2 outlets" not in markup


def test_corroboration_count_appears_once_the_outlet_list_collapses():
    rows = [entry(outlets=["Variety", "Deadline", "TechCrunch", "Digiday"], corroboration=4)]
    markup = render_html(brief(rows))
    assert "and 2 more" in markup
    assert "4 outlets" in markup


def test_needs_your_call_indexes_rather_than_duplicates():
    rows = [entry(), entry(title="Second story about creator payouts", needs_decision=True),
            entry(title="Third story")]
    markup = render_html(brief(rows))
    assert "Needs your call" in markup
    # The index points at story 02 rather than restating it as a third copy.
    assert markup.count("Second story about creator payouts") == 2


def test_empty_brief_still_renders_valid_shape():
    subj, text, markup = render(brief([]))
    assert subj
    assert "<h2" not in markup


def test_subject_uses_eastern_time_not_utc():
    # 01:30 UTC on the 22nd is still the evening of the 21st in New York.
    late = brief(generated_at=dt.datetime(2026, 9, 22, 1, 30, tzinfo=UTC), slot="2026-09-21T18")
    assert "21 Sep" in subject(late)


# --- feed text arrives pre-escaped; escaping it again is what Lauren saw ---

def test_feed_entities_do_not_reach_the_inbox_literally():
    """The reported glitch: `Here&#8217;s` printing as those exact characters."""
    from lozatron.render import _feed
    assert _feed("Here&#8217;s proof in pudding") == "Here’s proof in pudding"
    assert "&#8217;" not in _feed("Here&#8217;s proof")
    assert "&amp;" not in _feed("Here&#8217;s proof")


def test_rss_truncation_marker_is_dropped():
    from lozatron.render import _feed
    assert _feed("A story about a deal [&#8230;]") == "A story about a deal"
    assert _feed("A story about a deal […]") == "A story about a deal"


def test_embedded_feed_markup_is_stripped_not_shown():
    from lozatron.render import _feed
    assert _feed("<p>A <b>big</b> deal</p>") == "A big deal"


def test_double_encoded_feed_text_resolves():
    from lozatron.render import _feed
    assert _feed("Here&amp;#8217;s proof") == "Here’s proof"


def test_feed_cleaner_still_escapes_live_markup():
    """Unescaping must not be able to resurrect a working tag."""
    from lozatron.render import _feed
    out = _feed("&lt;script&gt;alert(1)&lt;/script&gt; a deal happened")
    assert "<script" not in out
    assert "&lt;script" in out


def test_analyst_text_is_not_unescaped():
    """Model prose is plain by contract; unescaping it could undo the wall."""
    from lozatron.render import _t
    assert _t("&lt;script&gt;") == "&amp;lt;script&amp;gt;"


def test_full_render_carries_no_literal_entities():
    rows = [entry(analysed=False, summary="Patreon&#8217;s new tier launched [&#8230;]")]
    markup = render_html(brief(rows))
    assert "&#8217;" not in markup
    assert "&#8230;" not in markup
    assert "Patreon’s new tier launched" in markup


def test_decision_index_is_one_line_not_a_repeat():
    """Sitting directly above its own story, a full headline reads as a bug."""
    long_title = ("'Hot Ones' Producer First We Feast Sets New Series "
                  "'Hot Ones: Game Night' Hosted by Joe Santagato (EXCLUSIVE)")
    rows = [entry(title=long_title, needs_decision=True), entry(title="Filler one"),
            entry(title="Filler two")]
    markup = render_html(brief(rows))
    assert "…" in markup
    # The full headline appears once, in the story itself, not twice.
    assert markup.count("Joe Santagato") == 1


def test_the_decision_section_is_dropped_when_it_flags_half_the_brief():
    """Flagging most of the brief is not triage.

    On the first live edition the model flagged two of three stories, which put
    a copy of most of the brief above the brief. If the flag does not
    discriminate that edition, it carried no information and is dropped.
    """
    rows = [entry(title="One", needs_decision=True),
            entry(title="Two", needs_decision=True),
            entry(title="Three")]
    assert brief(rows).decisions == []


def test_the_decision_section_survives_when_it_actually_discriminates():
    rows = [entry(title="One", needs_decision=True), entry(title="Two"),
            entry(title="Three")]
    assert [item.title for item in brief(rows).decisions] == ["One"]


def test_the_decision_section_is_capped():
    from lozatron.brief import Brief
    rows = [entry(title=f"Flagged {i}", needs_decision=True) for i in range(4)]
    rows += [entry(title=f"Plain {i}") for i in range(6)]
    assert len(brief(rows).decisions) == Brief.MAX_DECISIONS


def test_plain_text_decision_index_does_not_repeat_the_analysis():
    rows = [entry(title="One", needs_decision=True, why_it_matters="A very distinctive clause."),
            entry(title="Two"), entry(title="Three")]
    _, text, _ = render(brief(rows))
    assert "NEEDS YOUR CALL" in text
    # Once in the story body, never in the index above it.
    assert text.count("A very distinctive clause.") == 1
