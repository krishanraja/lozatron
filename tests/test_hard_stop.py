"""The reader-delivery hard stop and the daily email ceiling.

These two guards exist because a safety rule that lived in a workflow variable
was never wired into the workflow that needed it, and Lauren was flooded. Both
are now code, and these tests are what keeps them code: a change that silently
re-enables reader delivery, or removes the ceiling, fails here.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from lozatron import gmail
from lozatron.core import UTC, DeliveryState, Story


def story(key: str) -> Story:
    return Story(
        title=f"Title {key}",
        url=f"https://example.com/{key}",
        source="Example",
        summary="",
        published_at=dt.datetime(2026, 9, 22, 9, 0, tzinfo=UTC),
        score=10,
    )


@pytest.fixture
def stopped(monkeypatch):
    """Engage the kill switch, whatever the shipped default happens to be.

    These tests set the constant explicitly rather than relying on its current
    value. The switch is off in production today and on tomorrow; what has to
    keep working is the mechanism, so that re-engaging it stays a one-line
    change that provably does what it says.
    """
    monkeypatch.setattr(gmail, "READER_DELIVERY_ENABLED", False)


def test_reader_delivery_is_on_in_production():
    """Guards the other direction: a flip back to False mutes the product.

    Turning it off is a legitimate emergency action, but it has to be a
    decision someone made, not a line that drifted back in a merge.
    """
    assert gmail.READER_DELIVERY_ENABLED is True
    assert gmail.reader_delivery_enabled() is True


def test_recipients_are_ops_only_while_the_stop_is_engaged(stopped, monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com")
    monkeypatch.setenv("LOZ_OPS_EMAILS", "ops@example.com")
    assert gmail.recipients() == ["ops@example.com"]
    assert gmail.cc_recipients() == []


def test_the_stop_cannot_be_lifted_by_an_environment_variable(stopped, monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com")
    monkeypatch.setenv("LOZ_OPS_EMAILS", "ops@example.com")
    for name in ("LOZ_READER_DELIVERY", "LOZ_ENABLE_READER_DELIVERY", "LOZ_SEND_TO_READER"):
        monkeypatch.setenv(name, "true")
    assert gmail.recipients() == ["ops@example.com"]


def test_delivery_fails_loudly_rather_than_falling_back_to_the_reader(stopped, monkeypatch):
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com")
    monkeypatch.delenv("LOZ_OPS_EMAILS", raising=False)
    monkeypatch.delenv("LOZ_CC_EMAILS", raising=False)
    with pytest.raises(RuntimeError):
        gmail.recipients()


def test_lifting_the_stop_restores_the_configured_reader_list(monkeypatch):
    monkeypatch.setattr(gmail, "READER_DELIVERY_ENABLED", True)
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "lauren@example.com, other@example.com")
    assert gmail.recipients() == ["lauren@example.com", "other@example.com"]


def test_the_reader_gets_every_configured_address(monkeypatch):
    """Lauren has two. A brief reaching one of them is a half-delivery."""
    monkeypatch.setattr(gmail, "READER_DELIVERY_ENABLED", True)
    monkeypatch.setenv("LOZ_RECIPIENT_EMAILS", "one@example.com,two@example.com")
    monkeypatch.setenv("LOZ_CC_EMAILS", "krish@example.com")
    assert gmail.recipients() == ["one@example.com", "two@example.com"]
    assert gmail.cc_recipients() == ["krish@example.com"]


# --- the daily ceiling ---

def test_ceiling_permits_up_to_the_cap_and_no_further(tmp_path: Path):
    state = DeliveryState(tmp_path / "delivered.json")
    now = dt.datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    for index in range(DeliveryState.MAX_SENDS_PER_DAY):
        assert state.permits_send(now)
        state.mark([story(str(index))], now)
    assert not state.permits_send(now)


def test_the_ceiling_resets_on_a_new_utc_day(tmp_path: Path):
    state = DeliveryState(tmp_path / "delivered.json")
    now = dt.datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    for index in range(DeliveryState.MAX_SENDS_PER_DAY):
        state.mark([story(str(index))], now)
    assert not state.permits_send(now)
    assert state.permits_send(now + dt.timedelta(days=1))


def test_the_send_count_survives_a_round_trip(tmp_path: Path):
    path = tmp_path / "delivered.json"
    now = dt.datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    state = DeliveryState(path)
    state.mark([story("a")], now)
    state.save()
    again = DeliveryState(path).load()
    assert again.sends_today(now) == 1
    assert json.loads(path.read_text())["sends"] == {"2026-09-22": 1}


def test_a_version_2_file_loads_with_no_send_history(tmp_path: Path):
    path = tmp_path / "delivered.json"
    path.write_text(json.dumps({
        "version": 2,
        "last_success_date": "2026-09-21",
        "delivered": {},
        "slots": {},
    }))
    state = DeliveryState(path).load()
    assert state.sends == {}
    assert state.permits_send(dt.datetime(2026, 9, 22, 13, 0, tzinfo=UTC))


def test_prune_drops_send_counts_outside_the_retention_window(tmp_path: Path):
    state = DeliveryState(tmp_path / "delivered.json")
    now = dt.datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    state.sends = {"2026-09-01": 3, "2026-09-22": 1}
    state.prune(now)
    assert state.sends == {"2026-09-22": 1}
