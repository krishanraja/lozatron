"""Orchestrator behaviour: the slot gate and edition identity."""

import datetime as dt

from lozatron import app
from lozatron.core import DeliveryState, Story

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC)


def fake_collect(rows):
    return lambda now=None: (list(rows), [])


def story(title="Creator signs major brand partnership deal", hours=2):
    return Story(
        title=title,
        url="https://example.com/a",
        source="Test",
        published_at=dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC) - dt.timedelta(hours=hours),
        summary="Creator economy business update",
    )


def test_edition_id_is_slot_scoped_when_a_slot_is_known():
    now = dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC)
    assert app.edition_id("briefing", "2026-09-21T09", now) == "2026-09-21T09-briefing"


def test_edition_id_is_stable_across_retries_of_the_same_slot():
    a = app.edition_id("briefing", "2026-09-21T09", dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC))
    b = app.edition_id("briefing", "2026-09-21T09", dt.datetime(2026, 9, 21, 15, 40, tzinfo=UTC))
    assert a == b, "a delayed retry must resolve to the same edition, not a new one"


def test_skips_cleanly_when_no_slot_is_due(tmp_path, monkeypatch):
    state_path = tmp_path / "delivered.json"
    state = DeliveryState(state_path).load()
    state.record_slot("2026-09-21T09", dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC))
    state.save()

    monkeypatch.setattr(app, "utcnow", lambda: dt.datetime(2026, 9, 21, 14, 0, tzinfo=UTC))
    called = {"collect": False}

    def spy(now=None):
        called["collect"] = True
        return [], []

    monkeypatch.setattr(app, "collect", spy)
    result = app.run("briefing", state_path, tmp_path / "spend.json", dry_run=True, slot_gate=True)

    assert result["skipped"] == "not_due"
    assert result["sent"] is False
    assert called["collect"] is False, "a skipped run must not spend time or money collecting"


def test_delayed_run_still_delivers_its_slot(tmp_path, monkeypatch):
    """A 09:00 ET slot run arriving at 11:05 ET must still go out."""
    monkeypatch.setattr(app, "utcnow", lambda: dt.datetime(2026, 9, 21, 15, 5, tzinfo=UTC))
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    result = app.run("briefing", tmp_path / "d.json", tmp_path / "s.json",
                     dry_run=True, slot_gate=True)
    assert result["slot"] == "2026-09-21T09"
    assert result["stories_selected"] == 1


def test_slot_is_recorded_only_after_a_successful_send(tmp_path, monkeypatch):
    state_path = tmp_path / "delivered.json"
    monkeypatch.setattr(app, "utcnow", lambda: dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC))
    monkeypatch.setattr(app, "collect", fake_collect([story()]))

    def boom(*a, **k):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(app.gmail, "send", boom)
    try:
        app.run("briefing", state_path, tmp_path / "s.json", dry_run=False, slot_gate=True)
    except RuntimeError:
        pass
    assert DeliveryState(state_path).load().delivered_slots() == set(), \
        "a failed send must leave the slot outstanding so the next run retries it"


def test_successful_send_records_slot_and_fingerprints(tmp_path, monkeypatch):
    state_path = tmp_path / "delivered.json"
    monkeypatch.setattr(app, "utcnow", lambda: dt.datetime(2026, 9, 21, 13, 5, tzinfo=UTC))
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    monkeypatch.setattr(app.gmail, "send", lambda *a, **k: "msg-1")

    result = app.run("briefing", state_path, tmp_path / "s.json", dry_run=False, slot_gate=True)
    assert result["sent"] is True

    saved = DeliveryState(state_path).load()
    assert saved.delivered_slots() == {"2026-09-21T09"}
    assert len(saved.keys()) == 1


def test_gate_is_off_by_default_so_dispatch_always_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "utcnow", lambda: dt.datetime(2026, 9, 21, 3, 0, tzinfo=UTC))
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    result = app.run("briefing", tmp_path / "d.json", tmp_path / "s.json", dry_run=True)
    assert "skipped" not in result
    assert result["slot"] is None


# --- preview must never reach the brief's reader ---

def test_preview_sends_only_to_ops_never_to_recipients(tmp_path, monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com")
    monkeypatch.setenv("LOZ_OPS_EMAILS", "krish@example.com")
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    captured = {}

    def spy(subject, text, html, *, to=None, cc=None, **kw):
        captured["to"] = to
        captured["cc"] = cc
        captured["subject"] = subject
        return "msg-preview"

    monkeypatch.setattr(app.gmail, "send", spy)
    result = app.preview("briefing", tmp_path / "delivered.json")

    assert captured["to"] == ["krish@example.com"]
    assert captured["cc"] == []
    assert "lauren@example.com" not in str(captured)
    assert captured["subject"].startswith("[PREVIEW]")
    assert result["sent"] is True


def test_preview_never_marks_state(tmp_path, monkeypatch):
    """Marking would suppress these stories from Lauren's next real brief."""
    state_path = tmp_path / "delivered.json"
    monkeypatch.setenv("LOZ_OPS_EMAILS", "krish@example.com")
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    monkeypatch.setattr(app.gmail, "send", lambda *a, **k: "msg")

    app.preview("briefing", state_path)
    assert DeliveryState(state_path).load().keys() == set()
    assert not state_path.exists() or DeliveryState(state_path).load().delivered_slots() == set()


def test_preview_without_ops_address_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("LOZ_OPS_EMAILS", raising=False)
    monkeypatch.delenv("LOZ_CC_EMAILS", raising=False)
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([story()]))

    def boom(*a, **k):
        raise AssertionError("must not send without an ops address")

    monkeypatch.setattr(app.gmail, "send", boom)
    result = app.preview("briefing", tmp_path / "d.json")
    assert result["sent"] is False and result["recipients"] == 0


# --- the safety rules are unconditional ---

def test_selection_is_clustered_even_with_every_flag_unset(tmp_path, monkeypatch):
    """The flood's first cause: LOZ_CLUSTERING was set on one workflow only.

    Selection must not consult any environment variable to decide whether the
    corroboration rule, the per-source cap and the score floor apply.
    """
    for name in ("LOZ_CLUSTERING", "LOZ_ANALYST", "LOZ_CORROBORATION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([story()]))
    result = app.run("briefing", tmp_path / "d.json", tmp_path / "s.json", dry_run=True)
    assert result["clustering"] is True


def test_an_uncorroborated_community_story_is_never_selected(tmp_path, monkeypatch):
    """What actually reached Lauren: a lone Reddit post, no second source."""
    lone = Story(
        title="Can't monetize my videos even after getting accepted",
        url="https://www.reddit.com/r/PartneredYoutube/x",
        source="r/PartneredYoutube",
        published_at=NOW - dt.timedelta(hours=1),
        summary="Creator monetization brand deal partnership revenue",
        tier="community",
    )
    monkeypatch.delenv("LOZ_CLUSTERING", raising=False)
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([lone]))
    result = app.run("briefing", tmp_path / "d.json", tmp_path / "s.json", dry_run=True)
    assert result["stories_selected"] == 0
    assert result["sent"] is False


def test_the_daily_ceiling_suppresses_a_send_beyond_the_cap(tmp_path, monkeypatch):
    path = tmp_path / "d.json"
    state = DeliveryState(path)
    state.sends = {NOW.date().isoformat(): DeliveryState.MAX_SENDS_PER_DAY}
    state.save()
    monkeypatch.setattr(app, "utcnow", lambda: NOW)
    monkeypatch.setattr(app, "collect", fake_collect([story()]))

    def boom(*a, **k):
        raise AssertionError("send attempted past the daily ceiling")

    monkeypatch.setattr(app.gmail, "send", boom)
    result = app.run("briefing", path, tmp_path / "s.json", dry_run=False)
    assert result["ceiling_hit"] is True
    assert result["sent"] is False
