"""The learned ranking profile: Lauren's taps, turned into small nudges.

WHAT THIS IS FOR

She taps "More like this" or "Not for me" under a story. This turns a run of
those taps into term weights that nudge relevance, so the brief converges on
what she actually reads rather than on what the vocabulary lists guess.

WHY IT IS BOUNDED RATHER THAN GATED

The original plan had a human approve every profile change by pull request,
because "a profile that silently drifts is how you wake up to a brief nobody
can explain". Krish asked for it automatic. Both concerns are real, and the
resolution is not to pick one: drift is only dangerous when it is unbounded
and invisible, so this is built to be neither.

  * Every weight is clamped to +/-MAX_WEIGHT, and the total adjustment any one
    story can receive is clamped again to +/-MAX_TOTAL. The base relevance of
    a delivered story runs roughly 4 to 12, so the profile can reorder the
    brief and it can push a marginal story over or under the floor. It cannot
    dominate the score, and no volume of feedback can drive a story to zero.
  * A term moves by one step per run, never straight to the limit, so a
    single afternoon of tapping cannot reshape the brief.
  * A term is only considered once at least MIN_OBSERVATIONS signals mention
    it. One tap is an accident; four is a preference. Below the threshold the
    loop deliberately does nothing, which is the correct output of a week with
    two taps in it.
  * The profile is a committed JSON file. Every change lands in git history
    with the run that made it, so "why is the brief like this" is answerable
    by reading a diff, and any bad week is revertible with `git revert`.

WHAT IT DELIBERATELY DOES NOT TOUCH

The mechanics track. `select_analysis` has its own gate and a hard cap of two,
and mechanics exists because her preference brief asks for the mechanics of
the business, not because it scores well. A few "not for me" taps on essays
would otherwise delete a whole section of the product, which is precisely the
kind of capability loss a bounded weight is supposed to prevent. If mechanics
is not earning its place that is a judgement for a person, not an arithmetic
consequence.

It also never learns from `keep` or `ask`. `keep` is a bookmark and says
nothing about wanting more of something; `ask` is a request for sourcing,
which is a different mechanism from ranking.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

# One step at a time, to a bounded limit. See the module docstring.
MAX_WEIGHT = 2
MAX_TOTAL = 3
STEP = 1
MIN_OBSERVATIONS = 4

# A term has to carry meaning on its own to be worth weighting. These are the
# words that appear in almost every headline on this beat, so a weight on them
# would be a weight on everything -- which is the same as no weight at all,
# except that it is invisible and confusing.
STOPWORDS = frozenset("""
a an and are as at be been but by for from has have how in into is it its of
on or that the their they this to was were what when which who will with
after before over under more less new now says said amid
creator creators creator's economy influencer influencers report reports
""".split())

TOKEN = re.compile(r"[a-z][a-z0-9'’-]{2,}")


def terms_in(title: str) -> set[str]:
    """The weightable terms in a headline.

    Lowercased, stopworded, and length-bounded. Deliberately crude: a smarter
    extractor would be harder to explain when the profile does something
    surprising, and explicability is the point of a committed profile.
    """
    found = set()
    for match in TOKEN.finditer(title.casefold()):
        word = match.group(0).strip("'’-")
        if len(word) >= 3 and word not in STOPWORDS:
            found.add(word)
    return found


class Profile:
    """Term weights, loaded from and saved to a committed JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.weights: dict[str, int] = {}
        self.updated_at: str = ""
        self.observations: int = 0

    def load(self) -> "Profile":
        try:
            raw = json.loads(self.path.read_text())
        except Exception:  # noqa: BLE001 - an absent or broken profile is "no profile"
            return self
        if not isinstance(raw, dict):
            return self
        if int(raw.get("version", 0)) > SCHEMA_VERSION:
            # A newer writer has been here. Reading it under old rules would
            # misapply weights, and a wrong profile is worse than none.
            return self
        weights = raw.get("weights")
        if isinstance(weights, dict):
            for term, value in weights.items():
                if isinstance(term, str) and isinstance(value, (int, float)):
                    self.weights[term] = _clamp(int(value), MAX_WEIGHT)
        self.updated_at = str(raw.get("updated_at", ""))
        self.observations = int(raw.get("observations", 0) or 0)
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "updated_at": self.updated_at,
            "observations": self.observations,
            # Sorted so a diff shows what changed rather than a reshuffle.
            "weights": dict(sorted(self.weights.items())),
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")

    def bonus(self, title: str) -> int:
        """The adjustment for one headline, clamped twice. Never unbounded."""
        if not self.weights:
            return 0
        total = sum(self.weights.get(term, 0) for term in terms_in(title))
        return _clamp(total, MAX_TOTAL)

    def learn(self, signals: Iterable[dict[str, Any]], *, now: dt.datetime) -> dict[str, Any]:
        """Fold a run of signals into the weights. Returns what changed.

        One step per term per run, and only for terms with enough signal
        behind them. The return value is the audit record: it is printed into
        the run summary and is the explanation attached to the commit.
        """
        more: dict[str, int] = {}
        less: dict[str, int] = {}
        seen: dict[str, int] = {}
        counted = 0

        for signal in signals:
            action = str(signal.get("action", ""))
            if action not in ("more", "less"):
                continue
            title = str(signal.get("story_title", ""))
            if not title.strip():
                continue
            counted += 1
            for term in terms_in(title):
                seen[term] = seen.get(term, 0) + 1
                if action == "more":
                    more[term] = more.get(term, 0) + 1
                else:
                    less[term] = less.get(term, 0) + 1

        changes: dict[str, dict[str, int]] = {}
        for term, observations in sorted(seen.items()):
            if observations < MIN_OBSERVATIONS:
                continue
            net = more.get(term, 0) - less.get(term, 0)
            if net == 0:
                continue
            before = self.weights.get(term, 0)
            after = _clamp(before + (STEP if net > 0 else -STEP), MAX_WEIGHT)
            if after == before:
                continue
            if after == 0:
                self.weights.pop(term, None)
            else:
                self.weights[term] = after
            changes[term] = {"from": before, "to": after, "signals": observations}

        self.observations = counted
        self.updated_at = now.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
        return {
            "signals_read": counted,
            "terms_seen": len(seen),
            "terms_eligible": sum(1 for n in seen.values() if n >= MIN_OBSERVATIONS),
            "changed": changes,
        }


def _clamp(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))
