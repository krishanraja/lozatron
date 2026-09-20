from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .apify import collect_paid
from . import gmail
from .core import DeliveryState, render_email, select_stories, utcnow
from .sources import collect


def run(mode: str, state_path: Path, spend_path: Path, dry_run: bool) -> dict[str, object]:
    now = utcnow()
    state = DeliveryState(state_path).load()
    stories, source_errors = collect(now)
    paid_changed = False
    if os.environ.get("LOZ_ENABLE_PAID_SOURCES", "").lower() == "true" and not dry_run:
        paid_stories, paid_errors, paid_changed = collect_paid(spend_path, now)
        stories.extend(paid_stories)
        source_errors.extend(paid_errors)
    window = 8 if mode == "breaking" else 48
    limit = 3 if mode == "breaking" else 10
    selected = select_stories(stories, state.keys(), now=now, window_hours=window, limit=limit)

    subject, text_body, html_body = render_email(mode, selected, now)
    sent = False
    message_id = ""
    should_send = bool(selected) or mode == "briefing"
    if should_send and not dry_run:
        message_id = gmail.send(subject, text_body, html_body)
        sent = True
        state.mark(selected, now)
        state.prune(now)
        state.save()

    result = {
        "mode": mode,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Lozatron email briefings")
    parser.add_argument("--mode", choices=("briefing", "breaking"), required=True)
    parser.add_argument("--state", type=Path, default=Path("state/delivered.json"))
    parser.add_argument("--spend-state", type=Path, default=Path("state/spend.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-credentials", action="store_true")
    args = parser.parse_args()

    if args.verify_credentials:
        gmail.verify_credentials()
        print(json.dumps({"credentials_valid": True}))
        return 0

    result = run(args.mode, args.state, args.spend_state, args.dry_run)
    output = json.dumps(result, indent=2, default=str)
    print(output)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(
                f"## Lozatron {args.mode}\n\n"
                f"- Sources seen: {result['sources_seen']}\n"
                f"- Stories selected: {result['stories_selected']}\n"
                f"- Email sent: {result['sent']}\n"
                f"- Source errors: {len(result['source_errors'])}\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
