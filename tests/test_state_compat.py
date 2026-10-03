"""The committed ledger is live production state. Loading it must never regress.

`state/delivered.json` is written by the workflow after every successful send.
If a schema change stopped an existing fingerprint from suppressing, Lauren
would be re-sent stories she has already read -- the duplicate-delivery failure
this system has history with. These tests run against the real file.
"""

import datetime as dt
import json
from pathlib import Path

from lozatron.core import DeliveryState

UTC = dt.timezone.utc
LIVE = Path(__file__).resolve().parent.parent / "state" / "delivered.json"


def test_live_ledger_is_a_version_this_code_can_read():
    """Any version from 1 up to the code's own schema, never a hard-coded list.

    This test used to read `in (1, 2)`. Version 3 shipped on 22 September and
    the first send after it, on 25 September, wrote a version 3 ledger. From
    the next run on, this assertion failed in the workflow's Test step, before
    the brief could run, and 33 runs over eight days sent nothing. Pinning the
    live file to literal versions turned a compatible upgrade into an outage.
    """
    version = json.loads(LIVE.read_text())["version"]
    assert isinstance(version, int)
    assert 1 <= version <= DeliveryState.SCHEMA_VERSION


def test_a_ledger_saved_by_this_code_passes_the_live_version_check(tmp_path):
    """The guard above must accept what `save()` writes, so a schema bump can
    never again fail the very next run."""
    path = tmp_path / "delivered.json"
    DeliveryState(path).load().save()
    version = json.loads(path.read_text())["version"]
    assert version == DeliveryState.SCHEMA_VERSION
    assert 1 <= version <= DeliveryState.SCHEMA_VERSION


def test_every_live_fingerprint_still_suppresses():
    raw = json.loads(LIVE.read_text())["delivered"]
    loaded = DeliveryState(LIVE).load().keys()
    assert loaded == set(raw)
    assert len(loaded) >= 7


def test_version_one_file_loads_without_slots(tmp_path):
    path = tmp_path / "delivered.json"
    path.write_text(json.dumps({
        "version": 1,
        "last_success_date": "2026-09-21",
        "delivered": {"a" * 64: "2026-09-21T12:00:00+00:00"},
    }))
    state = DeliveryState(path).load()
    assert state.keys() == {"a" * 64}
    assert state.delivered_slots() == set()


def test_upgrade_preserves_prior_fingerprints(tmp_path):
    path = tmp_path / "delivered.json"
    path.write_text(json.dumps({
        "version": 1,
        "last_success_date": "2026-09-21",
        "delivered": {"b" * 64: "2026-09-21T12:00:00+00:00"},
    }))
    state = DeliveryState(path).load()
    state.record_slot("2026-09-21T09", dt.datetime(2026, 9, 21, 13, 4, tzinfo=UTC))
    state.save()
    again = DeliveryState(path).load()
    assert "b" * 64 in again.keys()
    assert again.delivered_slots() == {"2026-09-21T09"}
    assert json.loads(path.read_text())["version"] == 3


def test_prune_keeps_recent_slots_and_drops_stale_ones(tmp_path):
    path = tmp_path / "delivered.json"
    now = dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    state = DeliveryState(path).load()
    state.record_slot("2026-09-20T18", now - dt.timedelta(days=1))
    state.record_slot("2026-07-01T09", now - dt.timedelta(days=82))
    state.prune(now)
    assert state.delivered_slots() == {"2026-09-20T18"}


def test_hours_since_success_reports_none_when_never_delivered(tmp_path):
    state = DeliveryState(tmp_path / "delivered.json").load()
    assert state.hours_since_success(dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)) is None


def test_hours_since_success_measures_newest_slot(tmp_path):
    now = dt.datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    state = DeliveryState(tmp_path / "delivered.json").load()
    state.record_slot("2026-09-20T18", now - dt.timedelta(hours=30))
    state.record_slot("2026-09-21T09", now - dt.timedelta(hours=3))
    assert round(state.hours_since_success(now)) == 3
