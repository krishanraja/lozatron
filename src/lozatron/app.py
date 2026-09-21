from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from . import analyst, gmail, render as render_mod, schedule, store
from .apify import collect_paid
from .brief import compose
from .cluster import select_clusters
from .core import (
    DeliveryState, candidate, eligible, passes_hard_gates, render_email,
    select_stories, utcnow,
)
from .sources import collect


def edition_id(mode: str, slot: str | None, now: dt.datetime) -> str:
    """Stable identity for one edition, used for the Gmail Message-ID.

    Slot-based when a slot is known, so a retry of the same slot resolves to the
    same message and cannot duplicate. Timestamped otherwise.
    """
    return f"{slot}-{mode}" if slot else f"{now:%Y-%m-%dT%H%M%S}Z-{mode}"


def run(
    mode: str,
    state_path: Path,
    spend_path: Path,
    dry_run: bool,
    *,
    slot_gate: bool = False,
) -> dict[str, object]:
    now = utcnow()
    state = DeliveryState(state_path).load()

    # Briefings are promised at fixed Eastern times, but GitHub's scheduler
    # delivers late and sometimes not at all. The gate asks which slot is
    # outstanding rather than what hour it is now, so a delayed run still
    # delivers and a delivered slot never fires twice.
    slot: str | None = None
    if slot_gate:
        slot = schedule.due_slot(
            now,
            state.delivered_slots(),
            slots=schedule.parse_slots(os.environ.get("LOZ_BRIEF_SLOTS_ET")),
        )
        if slot is None:
            return {
                "mode": mode,
                "dry_run": dry_run,
                "skipped": "not_due",
                "sent": False,
                "stories_selected": 0,
                "sources_seen": 0,
                "source_errors": [],
                "selected": [],
            }

    stories, source_errors = collect(now)
    paid_changed = False
    if os.environ.get("LOZ_ENABLE_PAID_SOURCES", "").lower() == "true" and not dry_run:
        paid_stories, paid_errors, paid_changed = collect_paid(spend_path, now)
        stories.extend(paid_stories)
        source_errors.extend(paid_errors)
    window = 8 if mode == "breaking" else 48
    limit = 3 if mode == "breaking" else 10

    # Counted for the "screened and set aside" line, which answers the standing
    # complaint that rejections were never explained.
    filtered = {
        "stale": sum(1 for item in stories if not passes_hard_gates(item, now, window)),
        "off-mandate": sum(
            1 for item in stories
            if passes_hard_gates(item, now, window) and not candidate(item, now, window)
        ),
    }
    # What Lauren has already been told, so novelty is judged against her
    # actual history rather than guessed. Empty until the archive is live,
    # which weakens novelty scoring without breaking anything.
    recent = store.recent() if store.configured() else []

    # Clustering collapses one event reported by several outlets into a single
    # entry. `to_mark` carries every member, so outlet B's copy is suppressed
    # the moment outlet A's ships -- which is how the cross-outlet duplicate
    # closes without introducing a second dedup concept.
    # off | shadow | live. Shadow calls the model, validates and logs the
    # result, and still ships the deterministic brief, so its output can be read
    # before Lauren ever sees it.
    analyst_mode = os.environ.get("LOZ_ANALYST", "off").strip().lower()
    if mode == "breaking":
        # Breaking exists to get three stories out fast. Running the model on
        # every 90-minute check quadruples spend and adds 16 daily chances for
        # the critical path to fail, for a format that gains nothing from it.
        analyst_mode = "off"
    clustering = os.environ.get("LOZ_CLUSTERING", "").lower() == "true" or analyst_mode != "off"
    if clustering:
        clusters = select_clusters(
            stories, state.contains, now=now, window_hours=window, limit=limit
        )
        selected = [cluster.leader for cluster in clusters]
        to_mark = [member for cluster in clusters for member in cluster.members]
        corroboration = {cluster.leader.key: cluster.corroboration for cluster in clusters}
    else:
        selected = select_stories(stories, state.keys(), now=now, window_hours=window, limit=limit)
        to_mark = selected
        corroboration = {}

    # The analyst scores and explains within the gated set. It never adds a
    # story, never reorders, and never decides how many ship.
    analysis, analysis_reason = (None, "off")
    document = None
    archive_reason = "not_attempted"
    if analyst_mode != "off" and clusters:
        ledger = analyst.LlmLedger(spend_path.parent / "llm_spend.json").load()
        analysis, analysis_reason = analyst.analyse(
            clusters, recent,
            ledger=ledger,
            cap_usd=float(os.environ.get("LOZ_LLM_DAILY_USD_CAP", "2.00") or 2.00),
        )

    if analyst_mode == "live" and analysis is not None:
        document = compose(
            clusters, analysis, mode=mode, slot=slot, now=now,
            degraded="" if analysis_reason in ("ok", "partial_analysis") else analysis_reason,
            filtered=filtered,
        )
        try:
            subject, text_body, html_body = render_mod.render(document)
        except ValueError:
            # The shape check failed. Fall all the way back rather than send
            # something whose structure we could not verify.
            analysis_reason = "render_shape_failed"
            subject, text_body, html_body = render_email(mode, selected, now)
    else:
        subject, text_body, html_body = render_email(mode, selected, now)
    edition = edition_id(mode, slot, now)
    sent = False
    message_id = ""
    # A briefing with nothing in it used to still send "No qualifying new
    # stories were found", which is three guaranteed emails a day regardless
    # of signal. Silence is the more useful message: a missing brief then
    # means something is wrong rather than nothing happened.
    should_send = bool(selected)
    if should_send and not dry_run:
        message_id = gmail.send(subject, text_body, html_body, edition_id=edition)
        sent = True
        state.mark(to_mark, now)
        if slot:
            state.record_slot(slot, now)
        state.prune(now)
        state.save()
        if document is not None:
            archive_reason = store.record(
                document, edition_id=edition, sent=True,
                model=analysis.model if analysis else "",
            )

    result = {
        "mode": mode,
        "slot": slot,
        "clustering": clustering,
        "corroboration": corroboration,
        "analyst_mode": analyst_mode,
        "analysis": analysis_reason,
        "archive": archive_reason,
        "filtered": filtered,
        "edition_id": edition,
        "dry_run": dry_run,
        "sources_seen": len(stories),
        "stories_selected": len(selected),
        "sent": sent,
        "message_id_present": bool(message_id),
        "source_errors": source_errors,
        "paid_state_changed": paid_changed,
        "selected": [item.to_dict() for item in selected],
    }
    return result


def compare(mode: str, state_path: Path) -> dict[str, object]:
    """Show what clustering would change, against the same collected input.

    Read-only: collects once, runs both selection paths over that identical
    input, and reports the difference. Nothing is sent, nothing is marked.
    """
    now = utcnow()
    state = DeliveryState(state_path).load()
    stories, source_errors = collect(now)
    window = 8 if mode == "breaking" else 48
    limit = 3 if mode == "breaking" else 10

    # Counted for the "screened and set aside" line, which answers the standing
    # complaint that rejections were never explained.
    filtered = {
        "stale": sum(1 for item in stories if not passes_hard_gates(item, now, window)),
        "off-mandate": sum(
            1 for item in stories
            if passes_hard_gates(item, now, window) and not candidate(item, now, window)
        ),
    }
    # What Lauren has already been told, so novelty is judged against her
    # actual history rather than guessed. Empty until the archive is live,
    # which weakens novelty scoring without breaking anything.
    recent = store.recent() if store.configured() else []

    flat = select_stories(stories, state.keys(), now=now, window_hours=window, limit=limit)
    clusters = select_clusters(stories, state.contains, now=now, window_hours=window, limit=limit)

    merged = [
        {
            "title": cluster.leader.title,
            "outlets": cluster.outlets,
            "corroboration": cluster.corroboration,
            "members": [member.title for member in cluster.members],
        }
        for cluster in clusters
        if cluster.corroboration > 1
    ]
    flat_titles = [item.title for item in flat]
    cluster_titles = [cluster.leader.title for cluster in clusters]
    return {
        "mode": mode,
        "sources_seen": len(stories),
        "source_errors": source_errors,
        "unclustered_count": len(flat),
        "clustered_count": len(clusters),
        "merged_events": merged,
        "only_in_unclustered": [t for t in flat_titles if t not in cluster_titles],
        "only_in_clustered": [t for t in cluster_titles if t not in flat_titles],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Lozatron email briefings")
    parser.add_argument("--mode", choices=("briefing", "breaking"), required=True)
    parser.add_argument("--state", type=Path, default=Path("state/delivered.json"))
    parser.add_argument("--spend-state", type=Path, default=Path("state/spend.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-credentials", action="store_true")
    parser.add_argument("--no-slot-gate", action="store_true",
                        help="Deliver regardless of the Eastern slot ledger")
    parser.add_argument("--compare", action="store_true",
                        help="Print clustered vs unclustered selection; sends nothing")
    args = parser.parse_args()

    if args.verify_credentials:
        gmail.verify_credentials()
        print(json.dumps({"credentials_valid": True}))
        return 0

    if args.compare:
        print(json.dumps(compare(args.mode, args.state), indent=2, default=str))
        return 0

    # Scheduled briefings go through the slot gate; a manual dispatch always
    # runs, so a human can force a delivery without fighting the ledger.
    slot_gate = (
        args.mode == "briefing"
        and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
        and not args.no_slot_gate
    )
    result = run(args.mode, args.state, args.spend_state, args.dry_run, slot_gate=slot_gate)
    output = json.dumps(result, indent=2, default=str)
    print(output)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            lines = [f"## Lozatron {args.mode}", ""]
            if result.get("skipped"):
                lines.append(f"- Skipped: {result['skipped']} (no Eastern slot outstanding)")
            else:
                lines += [
                    f"- Slot: {result.get('slot') or 'n/a'}",
                    f"- Sources seen: {result['sources_seen']}",
                    f"- Stories selected: {result['stories_selected']}",
                    f"- Email sent: {result['sent']}",
                    f"- Analyst: {result.get('analyst_mode')} ({result.get('analysis')})",
                    f"- Source errors: {len(result['source_errors'])}",
                ]
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
