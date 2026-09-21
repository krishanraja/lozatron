"""Feed parsing. Untested before now, and the layer most exposed to upstream change."""

import datetime as dt
import xml.etree.ElementTree as ET

from lozatron.sources import _first, _text, rss_stories

UTC = dt.timezone.utc

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Creator signs licensing deal</title>
    <updated>2026-09-21T11:00:00Z</updated>
    <published>2026-09-21T09:00:00Z</published>
    <summary>A creator economy licensing partnership.</summary>
    <link href="https://example.com/story"/>
  </entry>
</feed>"""


def test_first_prefers_published_over_updated_despite_document_order():
    """<updated> precedes <published> here; freshness must use <published>."""
    entry = ET.fromstring(ATOM)[0]
    assert _text(_first(entry, ("published", "pubdate", "date", "updated"))) == "2026-09-21T09:00:00Z"


def test_first_falls_back_through_the_preference_list():
    entry = ET.fromstring(
        '<entry><updated>2026-09-21T11:00:00Z</updated></entry>'
    )
    assert _text(_first(entry, ("published", "pubdate", "date", "updated"))) == "2026-09-21T11:00:00Z"


def test_first_returns_none_when_nothing_matches():
    assert _first(ET.fromstring("<entry><title>x</title></entry>"), ("published",)) is None


def test_atom_entry_parses_with_published_timestamp(monkeypatch):
    monkeypatch.setattr("lozatron.sources._fetch", lambda url, timeout=15: ATOM.encode())
    monkeypatch.setattr("lozatron.sources.FEEDS", (("Example", "https://example.com/feed"),))
    rows, errors = rss_stories(dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    assert errors == []
    assert len(rows) == 1
    assert rows[0].published_at == dt.datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    assert rows[0].url == "https://example.com/story"
    assert rows[0].source == "Example"


def test_a_dead_feed_degrades_to_an_error_not_a_crash(monkeypatch):
    def boom(url, timeout=15):
        raise TimeoutError("gone")

    monkeypatch.setattr("lozatron.sources._fetch", boom)
    monkeypatch.setattr("lozatron.sources.FEEDS", (("Dead", "https://dead.example/feed"),))
    rows, errors = rss_stories(dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    assert rows == []
    assert errors == ["Dead: TimeoutError"]


def test_future_dated_entries_beyond_tolerance_are_dropped(monkeypatch):
    future = ATOM.replace("2026-09-21T09:00:00Z", "2026-09-25T09:00:00Z")
    monkeypatch.setattr("lozatron.sources._fetch", lambda url, timeout=15: future.encode())
    monkeypatch.setattr("lozatron.sources.FEEDS", (("Example", "https://example.com/feed"),))
    rows, _ = rss_stories(dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    assert rows == []
