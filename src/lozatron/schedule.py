"""Eastern-time delivery slots with a grace window.

GitHub Actions cron is a best-effort queue. Observed scheduled runs on this
repository arrive 22 minutes to 2.6 hours after their nominal slot, and some
slots are dropped entirely. An exact-hour local check would therefore skip
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

# Wide enough to absorb the observed Actions delay, and far shorter than the
# 24-hour gap between slots, so a wholly missed slot is never resurrected on
# top of the next day's.
#
# 200 was sized against a 2.6-hour worst case and was not enough. The scheduled
# runs on 22 and 23 September arrived at 13:24 and 13:31 ET -- about four and a
# half hours late -- found the 09:00 window closed at 12:20, reported `not_due`
# and sent nothing. Two days of silence, from a gate whose entire job is to
# stop exactly that.
#
# Six hours closes at 15:00 ET. That is past every delay this repository has
# observed and still inside the working day, so a brief that arrives late
# arrives as a late morning brief rather than as an evening one.
GRACE_MINUTES = 360


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
