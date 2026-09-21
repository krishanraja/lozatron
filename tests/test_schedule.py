import datetime as dt
from zoneinfo import ZoneInfo

from lozatron.schedule import due_slot, parse_slots, slot_key

UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")


def utc(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=UTC)


def test_on_time_run_delivers_its_slot():
    # 13:00 UTC in September is 09:00 EDT.
    assert due_slot(utc(2026, 9, 21, 13, 2), set()) == "2026-09-21T09"


def test_delayed_run_still_delivers_the_slot():
    """The regression this module exists for.

    A 09:00 ET run delayed to 11:05 ET must still deliver the morning slot.
    An exact-hour check would skip it and Lauren would receive nothing.
    """
    assert due_slot(utc(2026, 9, 21, 15, 5), set()) == "2026-09-21T09"


def test_already_delivered_slot_does_not_fire_twice():
    assert due_slot(utc(2026, 9, 21, 15, 5), {"2026-09-21T09"}) is None


def test_slot_missed_beyond_grace_is_not_resurrected():
    # 09:00 EDT + 200 min grace expires at 12:20 ET (16:20 UTC).
    assert due_slot(utc(2026, 9, 21, 16, 21), set()) is None


def test_newest_due_slot_wins_when_two_are_outstanding():
    # 18:00 EDT = 22:00 UTC. Both 14:00 and 18:00 are unrecorded; take 18.
    assert due_slot(utc(2026, 9, 21, 22, 1), set()) == "2026-09-21T18"


def test_slot_boundaries_follow_dst_not_fixed_offset():
    # Eastern is UTC-4 in summer and UTC-5 in winter. The same wall-clock slot
    # therefore maps to different UTC hours, which is the whole point of using
    # zoneinfo rather than cron arithmetic.
    assert due_slot(utc(2026, 7, 15, 13, 2), set()) == "2026-07-15T09"   # EDT
    assert due_slot(utc(2026, 1, 15, 13, 2), set()) is None              # 08:00 EST, not due
    assert due_slot(utc(2026, 1, 15, 14, 2), set()) == "2026-01-15T09"   # EST


def test_spring_forward_day_resolves():
    # 2026-03-08 is the US spring-forward date; 02:00 ET does not exist.
    assert due_slot(utc(2026, 3, 8, 13, 5), set()) == "2026-03-08T09"


def test_fall_back_day_resolves():
    # 2026-11-01 is the US fall-back date; 01:00 ET occurs twice.
    assert due_slot(utc(2026, 11, 1, 14, 5), set()) == "2026-11-01T09"


def test_slot_key_is_date_and_eastern_hour():
    assert slot_key(dt.datetime(2026, 9, 21, 18, 0, tzinfo=ET)) == "2026-09-21T18"


def test_parse_slots_falls_back_to_default_on_junk():
    assert parse_slots("9,14,18") == (9, 14, 18)
    assert parse_slots("18,9") == (9, 18)
    assert parse_slots("") == (9, 14, 18)
    assert parse_slots("banana,99") == (9, 14, 18)
