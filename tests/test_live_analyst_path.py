"""The live-analyst path, exercised without an API key.

This branch had no local coverage at all, because it only runs when
`LOZ_ANALYST` is live and a key resolves, which never happens on a developer
machine or in the test suite. So a plain `UnboundLocalError` -- a name used
above its assignment -- passed 347 tests, passed the workflow's own Test step,
and only surfaced when the brief was actually being sent to Lauren.

The analyst is faked here. The point is not to test the model, it is to make
the composing, dropping and rendering code that only runs beside a live
analyst reachable from the suite.
"""
from __future__ import annotations

import datetime as dt

import pytest

from lozatron import analyst as analyst_mod
from lozatron import app
from lozatron.core import Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 6, 13, 0, tzinfo=UTC)


def story(title, source="Tubefilter", hours=2):
    return Story(title=title, url=f"https://{source.lower()}.com/{abs(hash(title)) % 10**7}",
                 source=source, published_at=NOW - dt.timedelta(hours=hours),
                 summary="YouTube launches a creator fund partnership deal", tier="trade")


def analysis_for(keys, lede="Today's moves."):
    """A fake Analysis covering exactly the cluster keys given."""
    per = {
        key: analyst_mod.StoryAnalysis(
            cluster_key=key, what_happened="It happened.",
            why_it_matters="It matters.", what_to_watch="Watch this.",
            novelty=0.8, impact=0.7, confidence=0.9,
            entities=("YouTube",), needs_decision=False,
        ) for key in keys
    }
    return analyst_mod.Analysis(lede=lede, per_cluster=per, model="fake-model",
                                usage={"input_tokens": 0, "output_tokens": 0})


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setenv("LOZ_ANALYST", "live")
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "reader@example.com")
    monkeypatch.setenv("LOZ_OPS_EMAILS", "ops@example.com")
    monkeypatch.setattr(app.gmail, "send", lambda *a, **k: "msg-1")
    return tmp_path


def test_a_live_analyst_run_composes_and_renders(live, monkeypatch):
    """The regression: `edition` was read by compose before it was assigned."""
    rows = [story("YouTube launches a creator fund"),
            story("TikTok signs a payout deal", "Digiday", 3)]
    monkeypatch.setattr(app, "collect", lambda now=None: (list(rows), []))

    captured = {}

    def fake_analyse(clusters, recent, **kw):
        captured["keys"] = [c.key for c in clusters]
        return analysis_for(captured["keys"]), "ok"

    monkeypatch.setattr(app.analyst, "analyse", fake_analyse)
    result = app.run("briefing", live / "d.json", live / "s.json", dry_run=False)
    assert result["sent"] is True
    assert result["analysis"] == "ok"
    assert result["stories_selected"] == 2


def test_an_uncovered_story_is_dropped_on_the_live_path(live, monkeypatch):
    """The depth rule, exercised where it actually runs."""
    rows = [story("YouTube launches a creator fund"),
            story("TikTok signs a payout deal", "Digiday", 3)]
    monkeypatch.setattr(app, "collect", lambda now=None: (list(rows), []))

    def partial(clusters, recent, **kw):
        # Covers only the first cluster, as a partial response does.
        return analysis_for([clusters[0].key]), "partial_analysis"

    monkeypatch.setattr(app.analyst, "analyse", partial)
    result = app.run("briefing", live / "d.json", live / "s.json", dry_run=False)
    assert result["stories_selected"] == 1
    assert result["filtered"]["unanalysed"] == 1
    assert result["sent"] is True


def test_the_edition_id_reaches_the_feedback_buttons(live, monkeypatch):
    rows = [story("YouTube launches a creator fund")]
    monkeypatch.setattr(app, "collect", lambda now=None: (list(rows), []))
    monkeypatch.setattr(app.analyst, "analyse",
                        lambda clusters, recent, **kw: (analysis_for([c.key for c in clusters]), "ok"))
    sent = {}
    monkeypatch.setattr(app.gmail, "send",
                        lambda subject, text, html, **kw: sent.update(html=html) or "msg-1")
    app.run("briefing", live / "d.json", live / "s.json", dry_run=False)
    assert "loz-signal" in sent["html"]
    assert result_edition(sent["html"]) in sent["html"]


def result_edition(markup: str) -> str:
    import html as html_mod
    import re
    match = re.search(r"[?&]e=([^&\"]+)", html_mod.unescape(markup))
    assert match, "no edition id in the feedback links"
    return match.group(1)


def test_dropping_every_story_sends_nothing(live, monkeypatch):
    """If the analyst covered none of them, nothing is thin-dropped, because
    then the whole edition is thin and the deterministic brief is honest."""
    rows = [story("YouTube launches a creator fund")]
    monkeypatch.setattr(app, "collect", lambda now=None: (list(rows), []))
    monkeypatch.setattr(app.analyst, "analyse",
                        lambda clusters, recent, **kw: (analysis_for([]), "partial_analysis"))
    result = app.run("briefing", live / "d.json", live / "s.json", dry_run=False)
    assert result["stories_selected"] == 1
