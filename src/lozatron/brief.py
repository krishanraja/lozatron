"""Composition: turning gates, judgement and history into one document.

Deterministic. Everything the renderer needs is decided here, so the renderer
only has to escape and lay out. The model contributes prose to named fields and
nothing else: it does not choose the order, the count, or what appears.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from .core import clean_feed_text, clean_link

# Below this, the model is telling us it is unsure. Shown as a flag, never as a
# number: a self-rated confidence score invites trust in a figure that is not
# calibrated. The honest signal is how many independent outlets ran the story.
LOW_CONFIDENCE = 0.4


@dataclasses.dataclass(slots=True)
class Entry:
    title: str
    url: str
    outlets: list[str]
    corroboration: int
    age_hours: int
    summary: str
    # Identifies this story to the feedback endpoint, so a signal can be tied
    # back to the event rather than just the edition.
    cluster_key: str = ""
    # priority (0-8h) | secondary (8-24h) | fallback (24-48h). Lauren's rule
    # asks for the oldest tier to be labelled rather than silently mixed in.
    freshness: str = ""
    what_happened: str = ""
    why_it_matters: str = ""
    what_to_watch: str = ""
    low_confidence: bool = False
    needs_decision: bool = False
    analysed: bool = False

    @property
    def outlet_line(self) -> str:
        """'Variety and Deadline' reads better than a comma list at two."""
        names = self.outlets
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return f"{names[0]}, {names[1]} and {len(names) - 2} more"


@dataclasses.dataclass(slots=True)
class Brief:
    mode: str
    slot: str | None
    generated_at: dt.datetime
    entries: list[Entry]
    # The edition the feedback buttons report against. The endpoint accepts
    # only well-formed, recent edition ids, so this is also what bounds it.
    edition_id: str = ""
    lede: str = ""
    degraded: str = ""
    filtered: dict[str, int] = dataclasses.field(default_factory=dict)

    # The mechanics track: how something works, rather than what happened.
    # Lauren runs The Publish Press, so the news half of this brief competes
    # with her own product and this half does not. Capped and separate so it
    # can neither pad the news nor be padded by it.
    mechanics: list[Entry] = dataclasses.field(default_factory=list)

    # What individuals are saying. Never reporting, always labelled as such.
    # `commentary` counts chatter attached to a story we already have;
    # `patterns` are themes several distinct accounts raised with no story
    # behind them, stated in aggregate and never quoted post by post.
    commentary: dict[str, int] = dataclasses.field(default_factory=dict)
    patterns: list[str] = dataclasses.field(default_factory=list)

    # The "Needs your call" section is gone.
    #
    # It was an index: a clipped headline and a number pointing down at a
    # story a few screens below. Krish's verdict on seeing it in a real
    # edition was that it "doesn't really make sense at all", and he is right
    # for a reason worth writing down. It asked her to make a call without
    # ever saying what the call was, and it pointed at something already in
    # front of her. An index into a four-item list is not triage.
    #
    # The flag itself survives on the story, where it can say something,
    # rather than as a block above the brief restating it.

    @property
    def analysed(self) -> bool:
        return any(entry.analysed for entry in self.entries)

    def drop_thin_entries(self) -> list[Entry]:
        """Remove stories the analyst did not cover, when it covered others.

        On 6 October Lauren got four stories, three with the full what
        happened / why it matters / what to watch treatment and one as a bare
        feed blurb. Her brief is explicit on both counts: depth is part of the
        product and one-line summaries are too thin, and fewer strong stories
        beat a list containing weak ones. An uncovered story beside covered
        ones is the weak item.

        Only applies when the analyst actually produced something. If it
        failed for the whole edition, every story is thin together and the
        deterministic brief is the honest fallback, flagged as degraded.
        """
        if not self.analysed:
            return []
        thin = [entry for entry in self.entries if not entry.analysed]
        if thin:
            self.entries = [entry for entry in self.entries if entry.analysed]
        return thin


def compose(
    clusters: list[Any],
    analysis: Any | None,
    *,
    mode: str,
    slot: str | None,
    now: dt.datetime,
    degraded: str = "",
    filtered: dict[str, int] | None = None,
    edition_id: str = "",
    mechanics: list[Any] | None = None,
    commentary: dict[str, list[Any]] | None = None,
    patterns: list[Any] | None = None,
) -> Brief:
    """Build the document. Cluster order is preserved exactly as ranked."""
    def to_entry(cluster: Any) -> Entry:
        return Entry(
            # Titles arrive from feeds carrying entities -- `Alex Cooper&#8217;s`
            # renders literally once the escaper turns the `&` into `&amp;`.
            # Cleaned here, at composition, so the HTML renderer, the plain-text
            # twin and the decision index all get the same clean string rather
            # than each needing to remember.
            title=clean_feed_text(cluster.leader.title, 300),
            url=clean_link(cluster.leader.url),
            outlets=cluster.outlets,
            corroboration=cluster.corroboration,
            age_hours=cluster.age_hours,
            freshness=cluster.freshness,
            summary=cluster.leader.summary,
            cluster_key=getattr(cluster, "key", "") or "",
        )

    entries: list[Entry] = []
    for cluster in clusters:
        entry = to_entry(cluster)
        judged = analysis.per_cluster.get(cluster.key) if analysis else None
        if judged:
            entry.what_happened = judged.what_happened
            entry.why_it_matters = judged.why_it_matters
            entry.what_to_watch = judged.what_to_watch
            entry.low_confidence = judged.confidence < LOW_CONFIDENCE
            entry.needs_decision = judged.needs_decision
            entry.analysed = True
        entries.append(entry)

    mech_entries: list[Entry] = []
    for cluster in mechanics or []:
        entry = to_entry(cluster)
        judged = analysis.per_cluster.get(cluster.key) if analysis else None
        if judged:
            # Reuses the news fields deliberately rather than inventing a
            # parallel schema the analyst's validator would have to learn:
            # `why_it_matters` carries the mechanism and `what_to_watch`
            # carries how it applies. The renderer relabels them.
            entry.what_happened = judged.what_happened
            entry.why_it_matters = judged.why_it_matters
            entry.what_to_watch = judged.what_to_watch
            entry.analysed = True
        mech_entries.append(entry)

    return Brief(
        mode=mode,
        slot=slot,
        generated_at=now,
        entries=entries,
        lede=analysis.lede if analysis else "",
        degraded=degraded,
        filtered=filtered or {},
        edition_id=edition_id,
        mechanics=mech_entries,
        commentary={key: len(posts) for key, posts in (commentary or {}).items()},
        patterns=[str(item) for item in (patterns or [])],
    )
