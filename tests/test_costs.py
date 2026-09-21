"""Apify cost controls and the weekly report.

The controls exist because paid scraping used to fire on every non-dry run with
no mode check, which meant sixteen breaking-news checks a day could spend the
budget overnight, before the morning brief that is actually read.
"""

import datetime as dt
import json

import pytest

from lozatron import costs
from lozatron.apify import PROFILES, SpendState
from lozatron.app import paid_sources_due

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def paid_enabled(monkeypatch):
    monkeypatch.setenv("LOZ_ENABLE_PAID_SOURCES", "true")
    monkeypatch.delenv("LOZ_PAID_MIN_FREE", raising=False)
    monkeypatch.delenv("LOZ_BRIEF_SLOTS_ET", raising=False)


# --- when money may be spent at all ---

def test_breaking_runs_never_spend():
    """Sixteen checks a day, overnight, consuming the budget before the brief."""
    assert paid_sources_due("breaking", "2026-09-21T09", 0, dry_run=False) == (False, "breaking_mode")


def test_only_the_morning_brief_may_spend():
    assert paid_sources_due("briefing", "2026-09-21T09", 0, dry_run=False)[0] is True
    for slot in ("2026-09-21T14", "2026-09-21T18"):
        assert paid_sources_due("briefing", slot, 0, dry_run=False) == (False, "not_morning_slot")


def test_healthy_free_pool_skips_paid_sources():
    assert paid_sources_due("briefing", "2026-09-21T09", 9, dry_run=False) == (
        False, "free_sources_sufficient")


def test_thin_free_pool_permits_paid_sources():
    assert paid_sources_due("briefing", "2026-09-21T09", 2, dry_run=False) == (True, "due")


def test_dry_run_never_spends():
    assert paid_sources_due("briefing", "2026-09-21T09", 0, dry_run=True) == (False, "dry_run")


def test_disabled_flag_wins(monkeypatch):
    monkeypatch.setenv("LOZ_ENABLE_PAID_SOURCES", "false")
    assert paid_sources_due("briefing", "2026-09-21T09", 0, dry_run=False) == (False, "disabled")


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("LOZ_PAID_MIN_FREE", "2")
    assert paid_sources_due("briefing", "2026-09-21T09", 3, dry_run=False)[0] is False


# --- caps ---

def test_every_profile_carries_a_hard_apify_side_charge_cap():
    """The local ledger decides whether to start a run, never how much it bills."""
    for name, config in PROFILES.items():
        assert config["max_charge_usd"] > 0, name
        assert config["max_charge_usd"] >= config["estimate_usd"], name
        assert config["max_items"] > 0, name


def test_monthly_cap_blocks_even_when_the_day_is_clear(tmp_path):
    state = SpendState(tmp_path / "s.json", "2026-09-21").load()
    state.all_runs = [
        {"date": f"2026-09-{day:02d}", "profile": "x_creator", "actual_usd": 1.0}
        for day in range(1, 9)
    ]
    assert state.spent() == 0.0, "nothing spent today"
    assert not state.permits("x_creator", 1.00, 8.00), "the month is already at its ceiling"


def test_settled_charge_replaces_the_estimate_for_cap_arithmetic(tmp_path):
    state = SpendState(tmp_path / "s.json", "2026-09-21").load()
    row = state.record("youtube_community")          # reserves $0.85
    assert state.spent() == pytest.approx(0.85)
    state.settle(row, status="succeeded", actual_usd=0.12, run_id="abc")
    assert state.spent() == pytest.approx(0.12), "the real charge is what counts"


def test_a_failed_run_still_counts_against_the_cap(tmp_path):
    state = SpendState(tmp_path / "s.json", "2026-09-21").load()
    row = state.record("reddit_creator")
    state.settle(row, status="failed")
    assert state.spent() == pytest.approx(0.18), "a failed run has still billed"


def test_run_id_is_recorded_for_reconciliation(tmp_path):
    path = tmp_path / "s.json"
    state = SpendState(path, "2026-09-21").load()
    state.settle(state.record("x_creator"), status="succeeded", actual_usd=0.004, run_id="r1")
    state.save()
    assert json.loads(path.read_text())["runs"][0]["run_id"] == "r1"


# --- the weekly report ---

def seed(path, rows):
    path.write_text(json.dumps({"version": 1, "runs": rows}))
    return path


def test_report_totals_settled_charges(tmp_path):
    seed(tmp_path / "s.json", [
        {"date": "2026-09-20", "profile": "x_creator", "estimated_usd": 0.007, "actual_usd": 0.004,
         "status": "succeeded"},
        {"date": "2026-09-19", "profile": "reddit_creator", "estimated_usd": 0.18,
         "actual_usd": 0.17, "status": "succeeded"},
    ])
    report = costs.gather(tmp_path / "s.json", NOW)
    assert report["total"] == pytest.approx(0.174)
    assert report["runs"] == 2 and report["estimated_only"] == 0


def test_report_flags_runs_apify_has_not_settled(tmp_path):
    seed(tmp_path / "s.json", [
        {"date": "2026-09-20", "profile": "reddit_creator", "estimated_usd": 0.18, "status": "failed"},
    ])
    report = costs.gather(tmp_path / "s.json", NOW)
    assert report["estimated_only"] == 1
    _, text, html_body = costs.render(report, caps={"daily": 1.0, "monthly": 8.0})
    assert "not yet settled" in text and "not yet settled" in html_body


def test_report_excludes_runs_outside_the_window(tmp_path):
    seed(tmp_path / "s.json", [
        {"date": "2026-09-20", "profile": "x_creator", "actual_usd": 0.01},
        {"date": "2026-09-01", "profile": "x_creator", "actual_usd": 9.99},
    ])
    assert costs.gather(tmp_path / "s.json", NOW)["total"] == pytest.approx(0.01)


def test_comparison_is_in_dollars_not_percent(tmp_path):
    """Three cents to a dollar is 'up 3631%', which reads like a crisis."""
    seed(tmp_path / "s.json", [
        {"date": "2026-09-20", "profile": "x_creator", "actual_usd": 1.16},
        {"date": "2026-09-10", "profile": "x_creator", "actual_usd": 0.03},
    ])
    _, text, _ = costs.render(costs.gather(tmp_path / "s.json", NOW),
                              caps={"daily": 1.0, "monthly": 8.0})
    assert "up from $0.03" in text
    assert "%" not in text


def test_quiet_week_says_so_rather_than_showing_an_empty_table(tmp_path):
    seed(tmp_path / "s.json", [])
    subject, text, html_body = costs.render(costs.gather(tmp_path / "s.json", NOW),
                                            caps={"daily": 1.0, "monthly": 8.0})
    assert "$0.00" in subject
    assert "No paid runs this week" in text and "No paid runs this week" in html_body


def test_single_run_is_not_pluralised(tmp_path):
    seed(tmp_path / "s.json", [
        {"date": "2026-09-20", "profile": "x_creator", "actual_usd": 0.01},
    ])
    _, text, _ = costs.render(costs.gather(tmp_path / "s.json", NOW),
                              caps={"daily": 1.0, "monthly": 8.0})
    assert "1 run " in text and "1 runs" not in text


def test_report_never_addresses_the_brief_recipients(monkeypatch):
    """Cost mail is operational and must not reach the reader of the brief."""
    from lozatron import gmail
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com")
    monkeypatch.setenv("LOZ_CC_EMAILS", "krish@example.com")
    monkeypatch.delenv("LOZ_OPS_EMAILS", raising=False)
    assert gmail.ops_recipients() == ["krish@example.com"]
    monkeypatch.setenv("LOZ_OPS_EMAILS", "ops@example.com")
    assert gmail.ops_recipients() == ["ops@example.com"]
