"""The mandate gate must read the article, not the publisher's footer.

Feeds append promotional blocks to every item's description. Measured live,
all three Axios stories that passed the old gate were political: "Many ex-aides
to Harris betting against a 2028 run" matched on `youtube`, `launch`, `signs`
and `hiring`, every one of which came from the "follow us on Instagram and
YouTube / subscribe to our newsletter / we're hiring" footer rather than the
article. Influencer Marketing Hub carries 2,363 characters of it per item.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

from lozatron.core import GATE_SUMMARY_CHARS, Story, candidate, eligible, gate_text

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

FOOTER = (
    " Go deeper with Axios. Follow us on Instagram, YouTube and TikTok. "
    "Subscribe to our free newsletter for more. We're hiring — see open roles. "
    "Our partners launch new brand deals with us every week."
)


def story(title, summary="", hours=2, tier="trade"):
    return Story(
        title=title, url="https://example.com/a", source="Test",
        published_at=NOW - dt.timedelta(hours=hours), summary=summary, tier=tier,
    )


def test_a_political_story_with_a_creator_footer_is_rejected():
    item = story(
        "Many ex-aides to Harris betting against a 2028 run",
        "Many of those who know Kamala Harris best think she ultimately won't "
        "run for the White House in 2028." + FOOTER,
    )
    assert not eligible(item, NOW, 48)
    assert not candidate(item, NOW, 48)


def test_a_consumer_tech_story_with_a_newsletter_footer_is_rejected():
    item = story(
        "Transform Chrome's new tab page into the ultimate bookmark launchpad",
        "Whenever I install a new web browser I replace the new tab page." + FOOTER,
    )
    assert not eligible(item, NOW, 48)


def test_a_genuine_story_still_passes_with_the_same_footer_attached():
    """The footer must not be able to reject a real story either."""
    item = story(
        "YouTube launches a creator fund for Shorts",
        "YouTube said the fund will pay creators a share of Shorts ad revenue." + FOOTER,
    )
    assert eligible(item, NOW, 48)


def test_the_creator_term_must_be_in_the_headline():
    # Business event, creator relevance only in the body: not this brief's job.
    buried = story(
        "Japan Pitch Heads to Busan Film Festival With Four Co-Productions",
        "The festival signed a licensing deal; a YouTube channel will carry clips.",
    )
    assert not eligible(buried, NOW, 48)


def test_gate_text_truncates_the_summary():
    item = story("YouTube deal", "x" * 5000)
    assert len(gate_text(item)) <= len("YouTube deal ") + GATE_SUMMARY_CHARS


def test_business_terms_past_the_cap_do_not_count():
    """A footer sitting past the cap cannot supply the business half."""
    item = story("A creator writes about their week", "padding. " * 60 + "acquisition funding launch")
    assert not eligible(item, NOW, 48)


# --- the parser fix ---

def test_a_feed_with_leading_whitespace_parses():
    """ElementTree refuses anything before the XML declaration.

    Nieman Lab serves a leading newline, so a live feed with 30 items looked
    like a permanent ParseError for as long as it was in the list.
    """
    raw = b'\n<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>' \
          b'<item><title>T</title><link>https://e.com/1</link>' \
          b'<pubDate>Mon, 22 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>'
    try:
        ET.fromstring(raw)
        raise AssertionError("expected ElementTree to reject the leading newline")
    except ET.ParseError:
        pass
    assert ET.fromstring(raw.lstrip()) is not None


# --- publisher boilerplate in the body ---

def test_wordpress_footers_are_stripped():
    """"The post X appeared first on Y" reached the rendered brief."""
    from lozatron.core import clean_feed_text
    got = clean_feed_text(
        "ISMG spent two decades building a profitable business. "
        "The post This Profitable B2B Media Company Wants to Grow 4X. "
        "Here's How. appeared first on A Media Operator .", 400)
    assert got == "ISMG spent two decades building a profitable business."


def test_other_common_footers_are_stripped():
    from lozatron.core import clean_feed_text
    for body, want in [
        ("Creators launched an alliance. Continue reading this story at Variety",
         "Creators launched an alliance."),
        ("YouTube changed payouts. Read the full story on our site",
         "YouTube changed payouts."),
        ("TikTok lost the ruling. The article was originally published by Reuters",
         "TikTok lost the ruling."),
    ]:
        assert clean_feed_text(body, 400) == want


def test_a_sentence_that_merely_contains_the_word_post_survives():
    from lozatron.core import clean_feed_text
    body = "A real sentence that mentions the post office and should survive."
    assert clean_feed_text(body, 400) == body
