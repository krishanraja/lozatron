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


def test_a_run_four_and_a_half_hours_late_still_delivers():
    """The two days of silence, in code.

    The scheduled runs on 22 and 23 September arrived at 17:24 and 17:31 UTC --
    13:24 and 13:31 ET. The grace window closed at 12:20, both reported
    `not_due`, and Lauren received nothing either day.
    """
    assert due_slot(utc(2026, 9, 22, 17, 24), set()) == "2026-09-22T09"
    assert due_slot(utc(2026, 9, 23, 17, 31), set()) == "2026-09-23T09"


def test_slot_missed_beyond_grace_is_not_resurrected():
    # 09:00 EDT + 360 min grace expires at 15:00 ET (19:00 UTC). Past that the
    # slot stays missed rather than arriving in the evening dressed as the
    # morning brief.
    assert due_slot(utc(2026, 9, 21, 19, 1), set()) is None


def test_newest_due_slot_wins_when_two_are_outstanding():
    # Multi-slot behaviour still matters: an operator can widen the schedule
    # with LOZ_BRIEF_SLOTS_ET, and a run that is late for two slots must
    # deliver the current one rather than the stale one.
    # 18:00 EDT = 22:00 UTC. Both 14:00 and 18:00 are unrecorded; take 18.
    slots = (9, 14, 18)
    assert due_slot(utc(2026, 9, 21, 22, 1), set(), slots=slots) == "2026-09-21T18"


def test_default_schedule_is_one_slot_a_day():
    # The flood: sixteen breaking checks plus three briefing slots. One brief a
    # day is what PRIORITIES.md asked for, and the default must encode it.
    from lozatron.schedule import SLOTS_ET
    assert SLOTS_ET == (9,)
    # 18:00 EDT = 22:00 UTC. Nothing is due, because there is no evening slot.
    assert due_slot(utc(2026, 9, 21, 22, 1), set()) is None


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


def test_every_cron_in_the_workflow_lands_inside_the_delivery_window():
    """The gate and the crons are two halves of one mechanism, kept in one
    repository and edited months apart. A cron that fires outside the window it
    is meant to open sends nothing and says `not_due`, which reads like a
    working system. So the workflow's own hours are checked against the gate.

    EST is where this bites: 13:00 UTC is 08:00 Eastern, before the slot opens,
    so at least one later firing has to carry the day in winter.
    """
    import re
    from pathlib import Path

    text = Path(__file__).resolve().parents[1].joinpath(
        ".github/workflows/briefings.yml").read_text(encoding="utf-8")
    hours = [int(h) for h in re.findall(r'- cron: "0 (\d+) \* \* \*"', text)]
    assert hours, "no scheduled crons found in the briefing workflow"
    # A key, not the word: the comment above the crons explains why it is absent.
    keys = [line.strip() for line in text.splitlines() if not line.strip().startswith("#")]
    assert not any(line.startswith("timezone:") for line in keys), \
        "Actions cron has no timezone; the key is accepted and then ignored"

    for month, day in ((7, 15), (1, 15)):          # EDT and EST
        delivered = {due_slot(utc(2026, month, day, hour), set()) for hour in hours}
        assert delivered - {None}, f"no cron delivers the slot on 2026-{month:02d}-{day:02d}"


def test_slot_key_is_date_and_eastern_hour():
    assert slot_key(dt.datetime(2026, 9, 21, 18, 0, tzinfo=ET)) == "2026-09-21T18"


def test_parse_slots_falls_back_to_default_on_junk():
    assert parse_slots("9,14,18") == (9, 14, 18)
    assert parse_slots("18,9") == (9, 18)
    assert parse_slots("") == (9,)
    assert parse_slots("banana,99") == (9,)
