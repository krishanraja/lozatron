"""Eastern-time delivery slots with a grace window.

GitHub Actions cron is a best-effort queue. Observed scheduled runs on this
repository arrive 22 minutes to more than seven hours after their nominal
time, and some are dropped entirely. An exact-hour local check would therefore skip
delivery on any delayed run and Lauren would get nothing at all.

The gate instead asks a different question: which slot boundary has most
recently passed, is still within grace, and has not already been delivered?
A late run still delivers its slot. A slot already recorded cannot fire twice.
A slot missed beyond grace stays missed rather than arriving in the evening
dressed as the morning brief.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

EASTERN = "America/New_York"
# One brief a day, at 9am Eastern. PRIORITIES.md asked for exactly this; three
# slots was an assumption nobody made. The slot ledger then guarantees one
# delivery per slot, so one slot means one email a day however many times
# GitHub fires the workflow.
SLOTS_ET: tuple[int, ...] = (9,)

# The window is sized to the day, not to the last delay anybody measured.
#
# It was 200 minutes, sized against a 2.6-hour worst case; runs on 22 and 23
# September arrived 4.5 hours late and sent nothing. It became 360, sized
# against 4.5 hours; the run on 24 September arrived at 16:14 ET, an hour after
# that window closed, and sent nothing. Three days of silence from a gate whose
# whole job is to stop silence, each time because the window chased the last
# observed delay and GitHub produced a longer one.
#
# So the question is no longer "how late can Actions be?" but "until when is
# this still today's brief?". Twelve hours closes at 21:00 ET. A brief that
# lands mid-afternoon is late; a day with no brief at all is broken, and the
# reader cannot tell a quiet day from a failed one. Still far short of the
# 24-hour gap between slots, so a wholly missed day is never resurrected on
# top of the next one.
GRACE_MINUTES = 720


def slot_key(moment_et: dt.datetime) -> str:
    """Stable identifier for a delivery slot, e.g. '2026-09-21T09'."""
    return f"{moment_et.date().isoformat()}T{moment_et.hour:02d}"


def parse_slots(value: str | None) -> tuple[int, ...]:
    """Read a '9,14,18' style variable, falling back to the default."""
    if not value:
        return SLOTS_ET
    hours = []
    for part in value.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.append(int(part))
    return tuple(sorted(set(hours))) or SLOTS_ET


def slot_boundaries(now_et: dt.datetime, slots: tuple[int, ...]) -> list[dt.datetime]:
    """Every slot boundary on the Eastern day of `now_et` and the day before.

    Two days is sufficient: the grace window is always shorter than 24 hours.
    Boundaries are built with `fold=0` so a repeated wall-clock hour on the
    autumn DST transition resolves to the first occurrence rather than raising.
    """
    tz = now_et.tzinfo
    days = (now_et.date() - dt.timedelta(days=1), now_et.date())
    return sorted(
        dt.datetime(day.year, day.month, day.day, hour, tzinfo=tz, fold=0)
        for day in days
        for hour in slots
    )


def due_slot(
    now_utc: dt.datetime,
    delivered_slots: set[str] | frozenset[str],
    *,
    slots: tuple[int, ...] = SLOTS_ET,
    tz: str = EASTERN,
    grace_minutes: int = GRACE_MINUTES,
) -> str | None:
    """The slot this run should deliver, or None when nothing is due.

    Returns the most recent passed boundary that is inside the grace window and
    absent from `delivered_slots`. Newest first, so a run that is late for two
    slots delivers the current one rather than the stale one.
    """
    now_et = now_utc.astimezone(ZoneInfo(tz))
    grace = dt.timedelta(minutes=grace_minutes)
    for boundary in reversed(slot_boundaries(now_et, slots)):
        if boundary > now_et:
            continue
        if now_et - boundary > grace:
            break
        key = slot_key(boundary)
        if key not in delivered_slots:
            return key
    return None


def missed_slot(
    now_utc: dt.datetime,
    delivered_slots: set[str] | frozenset[str],
    *,
    slots: tuple[int, ...] = SLOTS_ET,
    tz: str = EASTERN,
    grace_minutes: int = GRACE_MINUTES,
) -> str | None:
    """The most recent slot whose window has closed without a delivery.

    `not_due` used to cover two very different states -- "too early, or
    already sent" and "the window closed and nothing went out" -- and both
    exited green. The second is the failure, and it read exactly like a
    working system for three days. This names it, so the run can fail loudly.

    Only the most recent passed boundary is considered: once a newer slot has
    opened, an older miss is history rather than something to keep alarming on.
    """
    now_et = now_utc.astimezone(ZoneInfo(tz))
    passed = [b for b in slot_boundaries(now_et, slots) if b <= now_et]
    if not passed:
        return None
    latest = passed[-1]
    if now_et - latest <= dt.timedelta(minutes=grace_minutes):
        return None
    key = slot_key(latest)
    return None if key in delivered_slots else key
