"""The feedback button, and the request box behind it.

Lauren wrote on 5 October 2026: "is there anyway for me to interact with the
AI? I prefer chatting to it and being able to have some control in pushing it
to gather stories" and "this view introduces friction where I need to go
through you." Every route to the system went through Krish.

Two one-tap verdicts under each story, and one request link per edition. The
button carries her choice, so the page opens with it selected: one tap,
optional note, send. Nothing is read from an inbox.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse

from lozatron.brief import Brief, Entry, compose
from lozatron.render import SIGNAL_URL, render, render_html

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
EDITION = "2026-10-06T09-briefing"


def entry(title="YouTube launches a creator fund", key="cl_abc123"):
    return Entry(title=title, url="https://example.com/a", outlets=["Tubefilter"],
                 corroboration=1, age_hours=3, summary="A creator fund.",
                 cluster_key=key, what_happened="It happened.",
                 why_it_matters="It matters.", what_to_watch="Watch this.",
                 analysed=True)


def brief(entries=None, edition=EDITION):
    return Brief(mode="briefing", slot="2026-10-06T09", generated_at=NOW,
                 entries=entries if entries is not None else [entry()],
                 edition_id=edition)


def links(markup):
    """Extract the feedback links as a browser would read them.

    `&` is correctly written `&amp;` inside an HTML attribute, so the raw
    markup has to be unescaped before the query string can be parsed. The
    markup is right; a test that reads it literally is not.
    """
    import html
    import re
    return [urllib.parse.urlparse(html.unescape(u)) for u in
            re.findall(r'href="([^"]*loz-signal[^"]*)"', markup)]


def test_each_story_carries_both_verdicts():
    markup = render_html(brief())
    actions = sorted({urllib.parse.parse_qs(u.query)["a"][0] for u in links(markup)})
    assert "more" in actions and "less" in actions


def test_the_brief_makes_no_promise_it_cannot_keep():
    """No free-text invitation until a host will serve the form.

    The brief used to carry a "Tell Lozatron what to chase" button pointing at
    a one-tap endpoint that could record no text at all. Supabase serves this
    project's responses as text/plain with a sandbox CSP whatever the function
    sets, so the form cannot render there and the invitation had nothing
    behind it. This asserts the invitation stays out until the page at
    web/api/index.js is hosted and LOZ_SIGNAL_URL points at it.
    """
    markup = render_html(brief())
    assert "Tell Lozatron what to chase" not in markup
    assert "ask" not in {urllib.parse.parse_qs(u.query)["a"][0] for u in links(markup)}


def test_a_verdict_identifies_the_story_not_just_the_edition():
    markup = render_html(brief())
    story_links = [u for u in links(markup)
                   if urllib.parse.parse_qs(u.query)["a"][0] in ("more", "less")]
    assert story_links
    for url in story_links:
        q = urllib.parse.parse_qs(url.query)
        assert q["e"] == [EDITION]
        assert q["c"] == ["cl_abc123"]
        assert q["t"][0].startswith("YouTube launches")


def test_the_links_carry_no_secret():
    """They are unsigned by necessity, so they must also be harmless."""
    markup = render_html(brief())
    for url in links(markup):
        q = urllib.parse.parse_qs(url.query)
        assert set(q) <= {"e", "a", "c", "t", "o"}
        for values in q.values():
            for value in values:
                assert "key" not in value.lower()
                assert not value.startswith("eyJ")   # a JWT


def test_an_edition_without_an_id_gets_no_buttons():
    """A preview or a test render must not emit links that cannot be honoured."""
    markup = render_html(brief(edition=""))
    assert not links(markup)


def test_the_plain_text_twin_carries_them_too():
    _, text, _ = render(brief())
    assert "More like this:" in text
    assert "TELL LOZATRON WHAT TO CHASE" not in text


def test_the_default_endpoint_is_the_deployed_one():
    assert SIGNAL_URL.endswith("/functions/v1/loz-signal")
    assert SIGNAL_URL.startswith("https://")


def test_the_endpoint_is_overridable_by_environment(monkeypatch):
    monkeypatch.setenv("LOZ_SIGNAL_URL", "https://example.test/hook")
    markup = render_html(brief())
    assert "https://example.test/hook" in markup


def test_buttons_do_not_change_the_story_count_the_shape_check_sees():
    """assert_render_shape counts one h2 per story; links must not add one."""
    from lozatron.render import assert_render_shape
    doc = brief([entry(), entry("TikTok tests a creator payout", "cl_def456")])
    assert_render_shape(render_html(doc), 2)


def test_compose_threads_the_edition_and_cluster_key_through():
    class FakeCluster:
        key = "cl_xyz789"
        leader = type("S", (), {"title": "A creator deal", "url": "https://e.com/x",
                                "summary": "s"})()
        outlets = ["Tubefilter"]
        corroboration = 1
        age_hours = 2
        freshness = "priority"
    doc = compose([FakeCluster()], None, mode="briefing", slot=None, now=NOW,
                  edition_id=EDITION)
    assert doc.edition_id == EDITION
    assert doc.entries[0].cluster_key == "cl_xyz789"


# --- the edition id contract, across two codebases ---

def test_every_edition_id_form_is_accepted_by_the_endpoint():
    """The bug this pins: the endpoint accepted only the slot form, but every
    forced or manual run emits the timestamp form, so the buttons on a
    manually sent edition were dead on arrival. The TypeScript validator in
    supabase/functions/loz-signal/index.ts mirrors EDITION_ID_RE."""
    from lozatron.app import edition_id
    from lozatron.core import EDITION_ID_RE
    moment = dt.datetime(2026, 10, 6, 16, 36, 21, tzinfo=UTC)
    forms = [
        edition_id("briefing", "2026-10-06T09", moment),
        edition_id("briefing", None, moment),
        edition_id("breaking", None, moment),
        edition_id("breaking", "2026-10-06T0906", moment),
    ]
    for value in forms:
        assert EDITION_ID_RE.match(value), value


def test_the_pattern_rejects_junk():
    from lozatron.core import EDITION_ID_RE
    for bad in ("nonsense", "2026-10-06-briefing", "2026-10-06T09-other",
                "../../etc/passwd", "2026-10-06T09-briefing' or 1=1"):
        assert not EDITION_ID_RE.match(bad), bad
