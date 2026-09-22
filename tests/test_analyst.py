"""The wall around the model.

No test here touches the network. Every one asserts that a bad response costs
the analysis layer and never the brief -- which is the whole reason the wall
exists. The September 2026 formatting collapse happened because a model
controlled presentation; rules 1-3 of `validate` are that incident in code.
"""

import datetime as dt
import json

import pytest

from lozatron import analyst
from lozatron.analyst import Analysis, LlmLedger, analyse, validate
from lozatron.cluster import Cluster
from lozatron.core import Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def cluster(key_title="Patreon acquires podcast startup Moment"):
    leader = Story(
        title=key_title, url="https://variety.com/x", source="Variety",
        published_at=NOW - dt.timedelta(hours=3), summary="A creator platform acquisition.",
    )
    from lozatron.cluster import canon_tokens
    tokens, anchors = canon_tokens(key_title)
    return Cluster(leader=leader, members=[leader], tokens=tokens, anchors=anchors, age_hours=3)


def good_story(key, **over):
    base = {
        "id": key,
        "what_happened": "Patreon acquired the podcast startup Moment for an undisclosed sum.",
        "why_it_matters": "It moves Patreon from memberships into podcast distribution directly.",
        "what_to_watch": "Whether Patreon bundles podcast hosting into existing creator tiers.",
        "novelty": 0.7, "impact": 0.8, "confidence": 0.6,
        "entities": ["Patreon"], "needs_decision": False,
    }
    base.update(over)
    return base


def payload(key, **over):
    return {"lede": "A quiet day with one structural move: Patreon buys its way into podcasting.",
            "stories": [good_story(key, **over)]}


# --- validation accepts what it should ---

def test_valid_response_is_accepted():
    c = cluster()
    lede, kept, dropped = validate(payload(c.key), {c.key})
    assert dropped == []
    assert kept[c.key].why_it_matters.startswith("It moves Patreon")
    assert lede


# --- validation rejects the individual story, not the whole response ---

@pytest.mark.parametrize("bad", [
    "See https://example.com for details on the acquisition terms and more.",
    "Patreon **acquired** Moment, a podcast startup, for an undisclosed sum.",
    "Patreon acquired Moment <b>today</b> for an undisclosed sum of money.",
    "Patreon acquired Moment.\n- It is a podcast startup with real traction.",
    "## Patreon acquired Moment, a podcast startup, for an undisclosed sum.",
    "Patreon acquired Moment via www.moment.fm for an undisclosed sum today.",
    "Visit [Moment](https://moment.fm) to see the podcast startup in question.",
])
def test_markup_and_links_drop_that_story(bad):
    c = cluster()
    _, kept, dropped = validate(payload(c.key, why_it_matters=bad), {c.key})
    assert kept == {}
    assert dropped == [c.key]


def test_hallucinated_cluster_id_is_dropped_silently():
    c = cluster()
    _, kept, dropped = validate(payload("cl_deadbeefdeadbeef"), {c.key})
    assert kept == {}
    assert dropped == ["cl_deadbeefdeadbeef"]


def test_one_bad_story_does_not_take_the_others_down():
    a, b = cluster(), cluster("Substack raises funding round from investors")
    raw = {"lede": "Two moves worth noting in the creator economy today overall.",
           "stories": [good_story(a.key, why_it_matters="See https://x.com for the full terms."),
                       good_story(b.key)]}
    _, kept, dropped = validate(raw, {a.key, b.key})
    assert list(kept) == [b.key]
    assert dropped == [a.key]


def test_out_of_range_scores_drop_the_story():
    c = cluster()
    _, kept, _ = validate(payload(c.key, novelty="not-a-number"), {c.key})
    assert kept == {}


def test_scores_are_clamped_into_range():
    c = cluster()
    _, kept, _ = validate(payload(c.key, impact=1.4, novelty=-0.2), {c.key})
    assert kept[c.key].impact == 1.0
    assert kept[c.key].novelty == 0.0


def test_too_short_a_field_is_rejected():
    c = cluster()
    _, kept, _ = validate(payload(c.key, what_to_watch="Soon."), {c.key})
    assert kept == {}


def test_a_bad_lede_is_dropped_without_losing_the_stories():
    c = cluster()
    lede, kept, _ = validate(
        {"lede": "Read more at https://example.com about today's moves in the creator economy.",
         "stories": [good_story(c.key)]}, {c.key})
    assert lede == ""
    assert kept


# --- the model is never given a link or a date ---

def test_the_model_payload_contains_no_url_and_no_timestamp():
    c = cluster()
    sent = analyst._payload([c], [{"title": "Older story", "date": "2026-09-14"}])
    assert "https://variety.com" not in sent
    assert "2026-09-21" not in sent
    assert '"age_hours": 3' in sent.replace(", ", ", ")
    assert "already_delivered" in sent


# --- failure paths all degrade, none raise ---

def test_missing_api_key_degrades(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result, reason = analyse([cluster()], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert result is None and reason == "no_api_key"


def test_no_stories_degrades(tmp_path):
    result, reason = analyse([], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert result is None and reason == "no_stories"


def test_budget_exhaustion_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ledger = LlmLedger(tmp_path / "l.json").load()
    ledger.reserve(2.00, "gpt-5")
    result, reason = analyse([cluster()], [], ledger=ledger, cap_usd=2.00)
    assert result is None and reason == "budget_exhausted"


@pytest.mark.parametrize("status,expected", [
    (401, "auth_failed"), (402, "quota_exhausted"),
    (429, "rate_limited"), (503, "provider_error"),
])
def test_provider_failures_are_named_not_generic(tmp_path, monkeypatch, status, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(*a, **k):
        raise http_error(status)

    monkeypatch.setattr(analyst.http, "request_json", boom)
    result, reason = analyse([cluster()], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert result is None and reason == expected


def http_error(status):
    from lozatron.http import HttpError
    return HttpError("boom", status=status)


def test_retired_model_falls_through_the_chain(tmp_path, monkeypatch):
    """A dead model id took a whole provider down in May 2026. Here it is a hop."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOZ_ANALYST_MODELS", "retired-model,working-model")
    c = cluster()
    seen = []

    def fake(url, **kw):
        model = json.loads(kw["data"])["model"]
        seen.append(model)
        if model == "retired-model":
            raise http_error(404)
        return {"choices": [{"message": {"content": json.dumps(payload(c.key))}}],
                "usage": {"total_tokens": 900}}

    monkeypatch.setattr(analyst.http, "request_json", fake)
    result, reason = analyse([c], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert seen == ["retired-model", "working-model"]
    assert reason == "ok" and result.model == "working-model"


def test_unparseable_content_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(analyst.http, "request_json",
                        lambda url, **kw: {"choices": [{"message": {"content": "{ truncated"}}]})
    result, reason = analyse([cluster()], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert result is None and reason == "unparseable_response"


def test_all_stories_rejected_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    c = cluster()
    bad = payload(c.key, what_happened="Read https://example.com for the details of this deal.")
    monkeypatch.setattr(analyst.http, "request_json",
                        lambda url, **kw: {"choices": [{"message": {"content": json.dumps(bad)}}]})
    result, reason = analyse([c], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert result is None and reason == "schema_rejected"


def test_partial_analysis_is_reported_as_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    a, b = cluster(), cluster("Substack raises funding round from investors")
    raw = {"lede": "Two moves worth noting in the creator economy today overall.",
           "stories": [good_story(a.key), good_story("cl_0000000000000000")]}
    monkeypatch.setattr(analyst.http, "request_json",
                        lambda url, **kw: {"choices": [{"message": {"content": json.dumps(raw)}}]})
    result, reason = analyse([a, b], [], ledger=LlmLedger(tmp_path / "l.json").load())
    assert reason == "partial_analysis" and result.partial


# --- spend ---

def test_spend_is_reserved_before_the_call(tmp_path, monkeypatch):
    """A timeout that still billed must not leave the cap looking untouched."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    path = tmp_path / "l.json"

    def boom(*a, **k):
        raise http_error(503)

    monkeypatch.setattr(analyst.http, "request_json", boom)
    analyse([cluster()], [], ledger=LlmLedger(path).load())
    assert LlmLedger(path).load().spent() > 0


def test_model_not_found_is_not_charged(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOZ_ANALYST_MODELS", "retired-only")
    path = tmp_path / "l.json"
    monkeypatch.setattr(analyst.http, "request_json",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(404)))
    result, reason = analyse([cluster()], [], ledger=LlmLedger(path).load())
    assert result is None and reason == "model_not_found"
    assert LlmLedger(path).load().spent() == 0.0


def test_a_bounded_field_does_not_cut_mid_word():
    """A live edition ended "...incubated and monet"."""
    from lozatron.analyst import MAX_FIELD, _clean
    got = _clean("word " * 400, MAX_FIELD)
    assert len(got) <= MAX_FIELD
    assert got.endswith("…")
    assert not got.endswith("wor…")


def test_truncation_lands_on_a_sentence_end_when_one_is_near_the_cut():
    from lozatron.analyst import _clean
    got = _clean("One sentence here. Two sentence here. " + "x" * 80, 60)
    assert got == "One sentence here. Two sentence here."
    # A complete sentence needs no ellipsis; only the ones after it were lost.
    assert not got.endswith("\u2026")


def test_a_short_field_is_untouched():
    from lozatron.analyst import _clean
    assert _clean("Short and complete.", 400) == "Short and complete."
