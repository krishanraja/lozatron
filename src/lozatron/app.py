from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from . import analyst, costs, gmail, render as render_mod, schedule, store
from .apify import DEFAULT_DAILY_CAP_USD, DEFAULT_MONTHLY_CAP_USD, collect_paid
from .brief import compose
from .cluster import select_clusters
from .core import (
    DeliveryState, candidate, eligible, env_flag, env_float, env_int, env_text,
    passes_hard_gates, render_email, select_stories, utcnow,
)
from .sources import collect


def edition_id(mode: str, slot: str | None, now: dt.datetime) -> str:
    """Stable identity for one edition, used for the Gmail Message-ID.

    Slot-based when a slot is known, so a retry of the same slot resolves to the
    same message and cannot duplicate. Timestamped otherwise.
    """
    return f"{slot}-{mode}" if slot else f"{now:%Y-%m-%dT%H%M%S}Z-{mode}"


def paid_sources_due(
    mode: str,
    slot: str | None,
    free_candidates: int,
    *,
    dry_run: bool,
) -> tuple[bool, str]:
    """Decide whether to spend money on this run. Returns (run_it, reason).

    Paid scraping used to fire on every non-dry run, with no mode check, which
    meant all sixteen breaking-news checks a day could spend the budget. Those
    run overnight, so the per-profile daily caps were routinely consumed by a
    4am alert before the 9am brief -- the one that is actually read -- ever
    started. Full price, worst possible delivery.

    So: one paid opportunity per day, on the morning brief, and only when the
    free sources came up short. Everything else is free.
    """
    if not env_flag("LOZ_ENABLE_PAID_SOURCES"):
        return False, "disabled"
    if dry_run:
        return False, "dry_run"
    if mode != "briefing":
        return False, "breaking_mode"
    morning = str(min(schedule.parse_slots(env_text("LOZ_BRIEF_SLOTS_ET"))))
    if slot and not slot.endswith(f"T{int(morning):02d}"):
        return False, "not_morning_slot"
    threshold = env_int("LOZ_PAID_MIN_FREE", 6)
    if free_candidates >= threshold:
        return False, "free_sources_sufficient"
    return True, "due"


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
            slots=schedule.parse_slots(env_text("LOZ_BRIEF_SLOTS_ET")),
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
    window = 8 if mode == "breaking" else 48
    limit = 3 if mode == "breaking" else 10

    # Count what the free sources produced before deciding to pay for more.
    free_candidates = sum(1 for item in stories if candidate(item, now, window))
    paid_changed = False
    run_paid, paid_reason = paid_sources_due(mode, slot, free_candidates, dry_run=dry_run)
    if run_paid:
        paid_stories, paid_errors, paid_changed = collect_paid(spend_path, now)
        stories.extend(paid_stories)
        source_errors.extend(paid_errors)

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
    analyst_mode = env_text("LOZ_ANALYST", "off").lower()

    # Selection is clustered, corroborated and capped unconditionally. It used
    # to sit behind LOZ_CLUSTERING, which was set on one of the two workflows;
    # the other kept running the ungated path and mailed Lauren uncorroborated
    # Reddit posts. A rule whose job is preventing bad mail must not be
    # switchable by a missing environment variable, so there is no longer a
    # variable and no longer an alternative path.
    #
    # `to_mark` carries every cluster member, so outlet B's copy is suppressed
    # the moment outlet A's ships -- the cross-outlet duplicate closes without
    # a second dedup concept.
    clustering = True
    clusters = select_clusters(
        stories, state.contains, now=now, window_hours=window, limit=limit
    )
    selected = [cluster.leader for cluster in clusters]
    to_mark = [member for cluster in clusters for member in cluster.members]
    corroboration = {cluster.leader.key: cluster.corroboration for cluster in clusters}

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
            cap_usd=env_float("LOZ_LLM_DAILY_USD_CAP", 2.00),
            monthly_cap_usd=env_float("LOZ_LLM_MONTHLY_USD_CAP", 40.00),
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
    # Fail-closed daily ceiling. It bounds every send path at once, so no
    # future gate, flag or workflow can produce a flood whatever else is
    # misconfigured.
    ceiling_hit = should_send and not state.permits_send(now)
    if ceiling_hit:
        should_send = False
        print(
            "::warning title=Daily email ceiling reached::"
            f"{state.sends_today(now)} brief(s) already sent today; suppressing."
        )
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
        "ceiling_hit": ceiling_hit,
        "sends_today": state.sends_today(now),
        "reader_delivery": gmail.reader_delivery_enabled(),
        "message_id_present": bool(message_id),
        "source_errors": source_errors,
        "paid_state_changed": paid_changed,
        "paid_sources": paid_reason,
        "free_candidates": free_candidates,
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


def preview(mode: str, state_path: Path) -> dict[str, object]:
    """Render a real brief and send it to the ops address only.

    Deliberately separate from `run`: it never reads or writes the delivery
    ledger and never resolves the brief's recipients, so a preview cannot reach
    Lauren and cannot mark a story as delivered. Marking would suppress those
    stories from her next real brief, which is the quiet way a preview does
    damage.
    """
    now = utcnow()
    state = DeliveryState(state_path).load()
    stories, source_errors = collect(now)
    window = 8 if mode == "breaking" else 48
    limit = 3 if mode == "breaking" else 10

    # Show what she would actually receive, so already-delivered stories are
    # still suppressed here.
    clusters = select_clusters(stories, state.contains, now=now, window_hours=window, limit=limit)

    analysis, reason = (None, "off")
    if env_text("LOZ_ANALYST", "off").lower() != "off" and clusters:
        ledger = analyst.LlmLedger(state_path.parent / "llm_spend.json").load()
        analysis, reason = analyst.analyse(
            clusters, [], ledger=ledger,
            cap_usd=env_float("LOZ_LLM_DAILY_USD_CAP", 2.00),
            monthly_cap_usd=env_float("LOZ_LLM_MONTHLY_USD_CAP", 40.00),
        )

    filtered = {
        "stale": sum(1 for item in stories if not passes_hard_gates(item, now, window)),
        "off-mandate": sum(
            1 for item in stories
            if passes_hard_gates(item, now, window) and not candidate(item, now, window)
        ),
    }
    document = compose(
        clusters, analysis, mode=mode, slot=None, now=now,
        degraded="" if reason in ("ok", "partial_analysis", "off") else reason,
        filtered=filtered,
    )
    subject, text_body, html_body = render_mod.render(document)
    subject = f"[PREVIEW] {subject}"

    to = gmail.ops_recipients()
    sent = False
    if to:
        gmail.send(subject, text_body, html_body, to=to, cc=[])
        sent = True
    return {
        "mode": mode,
        "preview": True,
        "sources_seen": len(stories),
        "stories": len(clusters),
        "analysis": reason,
        "analyst_model": analysis.model if analysis else "",
        "sent": sent,
        "recipients": len(to),
        "source_errors": source_errors,
        "titles": [cluster.leader.title for cluster in clusters],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Lozatron email briefings")
    parser.add_argument("--mode", choices=("briefing", "breaking"), default="briefing")
    parser.add_argument("--state", type=Path, default=Path("state/delivered.json"))
    parser.add_argument("--spend-state", type=Path, default=Path("state/spend.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-credentials", action="store_true")
    parser.add_argument("--no-slot-gate", action="store_true",
                        help="Deliver regardless of the Eastern slot ledger")
    parser.add_argument("--preview", action="store_true",
                        help="Render a real brief and send it to the ops address only")
    parser.add_argument("--cost-report", action="store_true",
                        help="Email the weekly Apify spend report to the ops address")
    parser.add_argument("--compare", action="store_true",
                        help="Print clustered vs unclustered selection; sends nothing")
    args = parser.parse_args()

    if args.verify_credentials:
        gmail.verify_credentials()
        print(json.dumps({"credentials_valid": True}))
        return 0

    if args.cost_report:
        report = costs.gather(args.spend_state, utcnow())
        caps = {
            "daily": env_float("LOZ_APIFY_DAILY_USD_CAP", DEFAULT_DAILY_CAP_USD),
            "monthly": env_float("LOZ_APIFY_MONTHLY_USD_CAP", DEFAULT_MONTHLY_CAP_USD),
        }
        subject, text_body, html_body = costs.render(report, caps=caps)
        to = gmail.ops_recipients()
        sent = False
        if to and not args.dry_run:
            gmail.send(subject, text_body, html_body, to=to, cc=[])
            sent = True
        print(json.dumps({**report, "sent": sent, "recipients": len(to)}, indent=2, default=str))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"## Lozatron Apify cost\n\n"
                    f"- Week: {report['start']} to {report['end']}\n"
                    f"- Total: ${report['total']:.2f} across {report['runs']} runs\n"
                    f"- Month to date: ${report['month_to_date']:.2f}\n"
                    f"- Emailed: {sent}\n"
                )
        if not to:
            print("::warning title=No ops recipients::Set LOZ_OPS_EMAILS or LOZ_CC_EMAILS.")
        return 0

    if args.preview:
        result = preview(args.mode, args.state)
        print(json.dumps(result, indent=2, default=str))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"## Lozatron preview ({args.mode})\n\n"
                    f"- Stories: {result['stories']}\n"
                    f"- Analyst: {result['analysis']} {result['analyst_model']}\n"
                    f"- Sent to ops: {result['sent']} ({result['recipients']} recipient(s))\n"
                )
        if not result["recipients"]:
            print("::warning title=No ops recipients::Set LOZ_OPS_EMAILS or LOZ_CC_EMAILS.")
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
                    f"- Paid sources: {result.get('paid_sources')}",
                    f"- Source errors: {len(result['source_errors'])}",
                ]
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
