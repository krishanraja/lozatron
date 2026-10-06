"""The learning loop: automatic, and bounded so that automatic is safe.

Krish asked for the brief to improve itself from Lauren's taps without a human
in the loop. His own plan had argued the opposite -- a pull request per change,
because "a profile that silently drifts is how you wake up to a brief nobody
can explain."

Drift is only dangerous when it is unbounded and invisible, so the resolution
was to make it neither rather than to pick a side. These tests pin the bounds,
because the bounds are the entire reason the loop is allowed to run unattended.
The clamp tests are the load-bearing ones: without them this is a knob that
feedback can turn until the product disappears.
"""
from __future__ import annotations

import datetime as dt
import json

from lozatron import profile as profile_mod
from lozatron.profile import MAX_TOTAL, MAX_WEIGHT, MIN_OBSERVATIONS, Profile, terms_in

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 5, 40, tzinfo=UTC)


def signal(action: str, title: str) -> dict[str, str]:
    return {"action": action, "story_title": title, "cluster_key": None}


def profile(tmp_path) -> Profile:
    return Profile(tmp_path / "profile.json")


# -- term extraction -------------------------------------------------------

def test_stopwords_are_not_weightable():
    """A weight on a word in every headline is a weight on nothing."""
    terms = terms_in("The creator economy report says more creators are in it")
    assert "creator" not in terms
    assert "economy" not in terms
    assert "report" not in terms
    assert "the" not in terms


def test_real_subjects_survive_extraction():
    terms = terms_in("Dhar Mann raises $100M for his studio")
    assert {"dhar", "mann", "studio", "raises"} <= terms


# -- the threshold ---------------------------------------------------------

def test_one_tap_changes_nothing(tmp_path):
    """One tap is an accident. The correct output is no change."""
    learned = profile(tmp_path)
    report = learned.learn([signal("more", "Dhar Mann raises money")], now=NOW)
    assert report["changed"] == {}
    assert learned.weights == {}


def test_a_term_moves_once_it_clears_the_threshold(tmp_path):
    learned = profile(tmp_path)
    signals = [signal("more", f"Dhar Mann deal number {i}") for i in range(MIN_OBSERVATIONS)]
    report = learned.learn(signals, now=NOW)
    assert learned.weights.get("mann") == 1
    assert report["changed"]["mann"] == {"from": 0, "to": 1, "signals": MIN_OBSERVATIONS}


def test_opposing_taps_cancel(tmp_path):
    """Equal enthusiasm and dislike is not a preference."""
    learned = profile(tmp_path)
    signals = (
        [signal("more", f"Dhar Mann deal {i}") for i in range(3)]
        + [signal("less", f"Dhar Mann deal {i}") for i in range(3)]
    )
    learned.learn(signals, now=NOW)
    assert "mann" not in learned.weights


# -- the clamps, which are why this can run unattended --------------------

def test_a_term_moves_one_step_per_run(tmp_path):
    """A single afternoon of tapping must not reshape the brief."""
    learned = profile(tmp_path)
    many = [signal("more", f"Dhar Mann deal {i}") for i in range(50)]
    learned.learn(many, now=NOW)
    assert learned.weights["mann"] == 1


def test_a_weight_cannot_exceed_the_limit(tmp_path):
    learned = profile(tmp_path)
    for _ in range(20):
        learned.learn([signal("more", f"Dhar Mann deal {i}") for i in range(10)], now=NOW)
    assert learned.weights["mann"] == MAX_WEIGHT


def test_a_weight_cannot_fall_below_the_limit(tmp_path):
    learned = profile(tmp_path)
    for _ in range(20):
        learned.learn([signal("less", f"Dhar Mann deal {i}") for i in range(10)], now=NOW)
    assert learned.weights["mann"] == -MAX_WEIGHT


def test_the_total_bonus_is_clamped_however_many_terms_match(tmp_path):
    """The one that stops feedback from dominating the score.

    Base relevance runs roughly 4 to 12. A headline matching ten liked terms
    must not collect ten times the weight, or the profile stops nudging the
    ranking and starts being the ranking.
    """
    learned = profile(tmp_path)
    learned.weights = {word: MAX_WEIGHT for word in
                       ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")}
    assert learned.bonus("Alpha bravo charlie delta echo foxtrot") == MAX_TOTAL


def test_the_total_penalty_is_clamped_too(tmp_path):
    learned = profile(tmp_path)
    learned.weights = {word: -MAX_WEIGHT for word in
                       ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")}
    assert learned.bonus("Alpha bravo charlie delta echo foxtrot") == -MAX_TOTAL


def test_no_amount_of_feedback_can_zero_a_story(tmp_path):
    """Capability loss is the failure this is built to prevent.

    A story's base relevance survives the worst the profile can do to it, so
    feedback reorders and can cross the floor, but cannot erase.
    """
    from lozatron.core import Story, relevance, reset_profile_cache

    story = Story(
        title="Influencer Jane Doe raises $12M for her studio",
        url="https://example.com/1", source="Tubefilter",
        published_at=NOW - dt.timedelta(hours=2),
        summary="A creator business story.", score=0, tier="trade",
    )
    base = relevance(story)

    hostile = profile(tmp_path)
    hostile.weights = {word: -MAX_WEIGHT for word in terms_in(story.title)}
    hostile.updated_at = NOW.isoformat()
    hostile.save()

    reset_profile_cache()
    import os
    os.environ["LOZ_PROFILE_PATH"] = str(hostile.path)
    try:
        worst = relevance(story)
    finally:
        os.environ.pop("LOZ_PROFILE_PATH", None)
        reset_profile_cache()

    assert worst == base - MAX_TOTAL
    assert worst > 0


# -- what it refuses to learn from ---------------------------------------

def test_keep_and_ask_teach_nothing(tmp_path):
    """`keep` is a bookmark; `ask` is a sourcing request, not a ranking one."""
    learned = profile(tmp_path)
    signals = (
        [signal("keep", f"Dhar Mann deal {i}") for i in range(10)]
        + [signal("ask", f"Dhar Mann deal {i}") for i in range(10)]
    )
    report = learned.learn(signals, now=NOW)
    assert report["signals_read"] == 0
    assert learned.weights == {}


def test_an_empty_headline_is_ignored(tmp_path):
    """The chase button records no title, and must not teach from a blank."""
    learned = profile(tmp_path)
    report = learned.learn([signal("more", "   ")] * 10, now=NOW)
    assert report["signals_read"] == 0


# -- persistence ---------------------------------------------------------

def test_a_saved_profile_round_trips(tmp_path):
    learned = profile(tmp_path)
    learned.learn([signal("more", f"Dhar Mann deal {i}") for i in range(MIN_OBSERVATIONS)],
                  now=NOW)
    learned.save()
    assert Profile(learned.path).load().weights == learned.weights


def test_the_saved_file_is_diffable(tmp_path):
    """Sorted keys, so a commit shows movement rather than a reshuffle."""
    learned = profile(tmp_path)
    learned.weights = {"zulu": 1, "alpha": -1, "mike": 2}
    learned.save()
    raw = json.loads(learned.path.read_text())
    assert list(raw["weights"]) == sorted(raw["weights"])
    assert raw["version"] == profile_mod.SCHEMA_VERSION


def test_a_missing_profile_is_simply_no_profile(tmp_path):
    assert Profile(tmp_path / "absent.json").load().weights == {}
    assert Profile(tmp_path / "absent.json").load().bonus("anything") == 0


def test_a_corrupt_profile_does_not_break_ranking(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{not json")
    assert Profile(path).load().bonus("anything") == 0


def test_a_future_schema_is_refused_rather_than_misread(tmp_path):
    """A wrong profile is worse than none."""
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(
        {"version": profile_mod.SCHEMA_VERSION + 1, "weights": {"mann": 99}}))
    assert Profile(path).load().weights == {}


def test_weights_loaded_from_disk_are_re_clamped(tmp_path):
    """A hand-edited or future-written file cannot smuggle in a huge weight."""
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"version": 1, "weights": {"mann": 500}}))
    assert Profile(path).load().weights["mann"] == MAX_WEIGHT


# -- the loop is wired, and cannot send mail -----------------------------

def test_learning_writes_nothing_when_nothing_changed(tmp_path, monkeypatch):
    """A quiet week leaves no commit, so history records real movement only."""
    from lozatron import app, store

    monkeypatch.setattr(store, "signals", lambda days=30: [])
    path = tmp_path / "profile.json"
    result = app.learn(path, days=30)
    assert result["wrote_profile"] is False
    assert not path.exists()


def test_learning_cannot_send_email():
    """Structural, not careful. `learn` must not reach gmail at all."""
    import ast
    import inspect
    import textwrap

    from lozatron import app

    tree = ast.parse(textwrap.dedent(inspect.getsource(app.learn)))
    reached = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert not any(name.startswith("gmail.") for name in reached)


def test_unreachable_supabase_changes_nothing(tmp_path, monkeypatch):
    """A learning failure must never degrade the brief."""
    from lozatron import app, store

    def boom(days=30):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(store, "signals", boom)
    path = tmp_path / "profile.json"
    # Must not raise. `store.signals` swallows its own failures today, so
    # `learn` catching as well is belt and braces -- but the rule is that
    # learning never breaks delivery, and a red loop over a transient blip
    # leaves somebody guessing.
    result = app.learn(path, days=30)
    assert result["read_error"] == "RuntimeError"
    assert result["wrote_profile"] is False
    assert not path.exists()
