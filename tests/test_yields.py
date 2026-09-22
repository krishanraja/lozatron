"""Per-source yield.

The question "is this feed earning its place" had no answer that did not
involve a human re-running measurements by hand. That gap is how Kajabi sat
in the list contributing nothing, and how a leading-newline ParseError hid a
live thirty-item feed behind one word in a swallowed error list.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from lozatron.yields import FIELDS, YieldLedger

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def counts(items=0, fresh=0, eligible=0, delivered=0):
    return {"items": items, "fresh": fresh, "eligible": eligible, "delivered": delivered}


def test_runs_on_the_same_day_accumulate(tmp_path: Path):
    led = YieldLedger(tmp_path / "y.json")
    led.record({"Tubefilter": counts(10, 8, 3, 1)}, NOW)
    led.record({"Tubefilter": counts(12, 9, 2, 1)}, NOW)
    assert led.window(NOW)["Tubefilter"] == counts(22, 17, 5, 2)


def test_the_window_excludes_older_days(tmp_path: Path):
    led = YieldLedger(tmp_path / "y.json")
    led.record({"Tubefilter": counts(10, 8, 3, 1)}, NOW - dt.timedelta(days=30))
    led.record({"Tubefilter": counts(1, 1, 1, 1)}, NOW)
    assert led.window(NOW, days=7)["Tubefilter"] == counts(1, 1, 1, 1)


def test_a_source_fetched_all_week_for_nothing_is_named(tmp_path: Path):
    led = YieldLedger(tmp_path / "y.json")
    led.record({"Kajabi": counts(280, 0, 0, 0),
                "NetInfluencer": counts(70, 70, 49, 9)}, NOW)
    assert led.freeloaders(NOW) == ["Kajabi"]


def test_a_quiet_week_is_not_a_freeloader(tmp_path: Path):
    """A weekly publication that happened to run nothing relevant is not the
    same as a daily feed supplying hundreds of items and no signal."""
    led = YieldLedger(tmp_path / "y.json")
    led.record({"A Media Operator": counts(6, 6, 0, 0)}, NOW)
    assert led.freeloaders(NOW) == []


def test_the_ledger_round_trips_and_prunes(tmp_path: Path):
    path = tmp_path / "y.json"
    led = YieldLedger(path)
    led.record({"Tubefilter": counts(10, 8, 3, 1)}, NOW - dt.timedelta(days=60))
    led.record({"Tubefilter": counts(2, 2, 1, 1)}, NOW)
    led.prune(NOW)
    led.save()
    again = YieldLedger(path).load()
    assert again.window(NOW, days=90)["Tubefilter"] == counts(2, 2, 1, 1)
    assert json.loads(path.read_text())["version"] == YieldLedger.SCHEMA_VERSION


def test_a_missing_or_corrupt_ledger_loads_empty(tmp_path: Path):
    """Yield reporting is a nicety; it must never be able to fail a brief."""
    assert YieldLedger(tmp_path / "absent.json").load().days == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert YieldLedger(bad).load().days == {}


def test_only_counts_are_stored(tmp_path: Path):
    """This file is committed to a public repository."""
    path = tmp_path / "y.json"
    led = YieldLedger(path)
    led.record({"Tubefilter": counts(10, 8, 3, 1)}, NOW)
    led.save()
    stored = json.loads(path.read_text())["days"]["2026-09-22"]["Tubefilter"]
    assert set(stored) == set(FIELDS)
    assert all(isinstance(value, int) for value in stored.values())
