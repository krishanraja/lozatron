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


def test_every_story_url_appears_exactly_once():
    rows = [entry(), entry(title="Second story about creator deals", url="https://deadline.com/b")]
    markup = render_html(brief(rows))
    for row in rows:
        assert markup.count(row.url) == 1


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
    rows = [entry(), entry(title="Second story about creator payouts", needs_decision=True)]
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
