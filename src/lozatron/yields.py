"""Per-source yield: which feeds are earning their place.

Until this existed there was no way to answer that question without a human
re-running the measurements by hand. That gap is how Kajabi sat in the source
list contributing nothing for weeks, and how a leading-newline `ParseError`
hid a live feed with thirty items behind the single word "ParseError" in a
swallowed error list.

Counts only -- items seen, items fresh, items that cleared the mandate gate,
items actually delivered. No titles, no URLs, nothing about what Lauren read.
This file is committed to a public repository, and the privacy boundary in
docs/MIGRATION.md says brief content never is.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .core import parse_datetime, utcnow

RETENTION_DAYS = 30

FIELDS = ("items", "fresh", "eligible", "delivered")


class YieldLedger:
    """A rolling per-day, per-source tally."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        self.path = path
        self.days: dict[str, dict[str, dict[str, int]]] = {}

    def load(self) -> "YieldLedger":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.days = dict(data.get("days", {}))
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            self.days = {}
        return self

    def record(self, rows: dict[str, dict[str, int]], when: dt.datetime | None = None) -> None:
        """Add one run's counts. Additive, because several runs share a day."""
        day = (when or utcnow()).date().isoformat()
        bucket = self.days.setdefault(day, {})
        for source, counts in rows.items():
            into = bucket.setdefault(source, {field: 0 for field in FIELDS})
            for field in FIELDS:
                into[field] = int(into.get(field, 0)) + int(counts.get(field, 0))

    def prune(self, when: dt.datetime | None = None, days: int = RETENTION_DAYS) -> None:
        cutoff = ((when or utcnow()) - dt.timedelta(days=days)).date().isoformat()
        self.days = {day: rows for day, rows in self.days.items() if day >= cutoff}

    def window(self, now: dt.datetime, days: int = 7) -> dict[str, dict[str, int]]:
        """Totals per source over the trailing window, newest day inclusive."""
        start = (now - dt.timedelta(days=days - 1)).date().isoformat()
        end = now.date().isoformat()
        out: dict[str, dict[str, int]] = {}
        for day, rows in self.days.items():
            if not (start <= day <= end):
                continue
            for source, counts in rows.items():
                into = out.setdefault(source, {field: 0 for field in FIELDS})
                for field in FIELDS:
                    into[field] += int(counts.get(field, 0))
        return out

    def freeloaders(self, now: dt.datetime, days: int = 7,
                    min_items: int = 20) -> list[str]:
        """Sources that were fetched plenty and delivered nothing.

        `min_items` guards against naming a source that simply had a quiet
        week. A weekly publication is not a freeloader; a daily feed that
        supplied two hundred items and nothing eligible is.
        """
        rows = self.window(now, days)
        return sorted(
            source for source, counts in rows.items()
            if counts["items"] >= min_items and counts["eligible"] == 0
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.SCHEMA_VERSION,
            "days": {day: dict(sorted(rows.items())) for day, rows in sorted(self.days.items())},
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
