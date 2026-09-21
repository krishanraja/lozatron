"""Spend ledger guarantees.

Two of these fail against the pre-fix code and are the reason it changed.
"""

import datetime as dt
import json

import pytest

from lozatron.apify import PROFILES, SpendState


def test_save_preserves_prior_days_ledger(tmp_path):
    path = tmp_path / "spend.json"
    old = SpendState(path, "2026-09-19").load()
    old.record("x_creator")
    old.save()

    today = SpendState(path, "2026-09-20").load()
    today.record("reddit_creator")
    today.save()

    rows = json.loads(path.read_text())["runs"]
    dates = {row["date"] for row in rows}
    assert dates == {"2026-09-19", "2026-09-20"}, "prior days must survive a later save"


def test_todays_view_excludes_other_days(tmp_path):
    path = tmp_path / "spend.json"
    seed = SpendState(path, "2026-09-19").load()
    seed.record("youtube_community")
    seed.save()

    today = SpendState(path, "2026-09-20").load()
    assert today.spent() == 0.0
    assert today.count("youtube_community") == 0
    assert len(today.all_runs) == 1


def test_reservation_counts_against_cap_even_without_settlement(tmp_path):
    """A billed-but-timed-out run must still consume budget.

    Recording after the call meant a timeout billed real money, recorded
    nothing, and let the next run launch the same actor again.
    """
    state = SpendState(tmp_path / "spend.json", "2026-09-20").load()
    row = state.record("youtube_community")          # reserved, never settled
    assert state.spent() == pytest.approx(0.85)
    assert not state.permits("reddit_creator", 1.00)
    state.settle(row, status="failed")
    assert state.spent() == pytest.approx(0.85), "a failed run is not refunded"


def test_settle_marks_status_and_survives_round_trip(tmp_path):
    path = tmp_path / "spend.json"
    state = SpendState(path, "2026-09-20").load()
    row = state.record("x_creator")
    state.settle(row, status="succeeded")
    state.save()
    assert json.loads(path.read_text())["runs"][0]["status"] == "succeeded"


def test_cap_is_fail_closed(tmp_path):
    state = SpendState(tmp_path / "spend.json", "2026-09-20").load()
    assert state.permits("youtube_community", 1.00)
    state.record("youtube_community")
    assert not state.permits("reddit_creator", 1.00)


def test_per_profile_run_cap_is_enforced(tmp_path):
    state = SpendState(tmp_path / "spend.json", "2026-09-20").load()
    for _ in range(PROFILES["x_creator"]["daily_cap"]):
        assert state.permits("x_creator", 10.00)
        state.record("x_creator")
    assert not state.permits("x_creator", 10.00), "run count cap applies below the dollar cap"


def test_prune_drops_rows_beyond_retention(tmp_path):
    path = tmp_path / "spend.json"
    state = SpendState(path, "2026-09-20").load()
    state.all_runs = [
        {"date": "2026-01-01", "profile": "x_creator", "estimated_usd": 0.007},
        {"date": "2026-09-19", "profile": "x_creator", "estimated_usd": 0.007},
    ]
    state.save()
    assert {row["date"] for row in json.loads(path.read_text())["runs"]} == {"2026-09-19"}


def test_corrupt_ledger_loads_as_empty(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text("{ not json")
    assert SpendState(path, "2026-09-20").load().all_runs == []
