"""Weekly Apify cost report.

Figures come from the delivery ledger, where every run is reconciled against the
charge Apify itself reported rather than the estimate this repository guessed.
Where a run has no settled charge yet, its reservation is used and the report
says so, because an unlabelled estimate presented as a bill is worse than no
report at all.

Scope is Lozatron's own runs. It is deliberately not account-wide Apify usage,
which would sweep in every other actor on the account and make the number
unattributable.
"""

from __future__ import annotations

import datetime as dt
import html
from collections import defaultdict
from pathlib import Path
from typing import Any

from .analyst import LlmLedger
from .apify import PROFILES, SpendState
from .yields import YieldLedger

INK = "#14171a"
PAPER = "#faf8f5"
MUTED = "#6b7075"
RULE = "#e4e0da"
FLAG = "#8a5a12"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"


def gather(spend_path: Path, now: dt.datetime, days: int = 7,
           yield_path: Path | None = None) -> dict[str, Any]:
    end = now.date()
    start = end - dt.timedelta(days=days - 1)
    state = SpendState(spend_path, end.isoformat()).load()

    rows = [
        row for row in state.all_runs
        if start.isoformat() <= str(row.get("date", "")) <= end.isoformat()
    ]
    by_profile: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "usd": 0.0, "settled": 0, "failed": 0}
    )
    for row in rows:
        bucket = by_profile[str(row.get("profile", "unknown"))]
        bucket["runs"] += 1
        bucket["usd"] += SpendState._cost(row)
        if isinstance(row.get("actual_usd"), (int, float)):
            bucket["settled"] += 1
        if str(row.get("status", "")) in ("failed", "aborted", "timed-out"):
            bucket["failed"] += 1

    total = round(sum(b["usd"] for b in by_profile.values()), 4)
    settled = sum(b["settled"] for b in by_profile.values())
    prior_end = start - dt.timedelta(days=1)
    prior_total, _ = state.spent_between(
        (prior_end - dt.timedelta(days=days - 1)).isoformat(), prior_end.isoformat()
    )
    llm = LlmLedger(spend_path.parent / "llm_spend.json", end.isoformat()).load().tokens_between(
        start.isoformat(), end.isoformat()
    )
    # Which feeds earned their place. Cheap to carry and the only thing that
    # turns source curation from a thing somebody measured once into a thing
    # the system reports on itself.
    sources: dict[str, Any] = {}
    if yield_path is not None:
        ledger = YieldLedger(yield_path).load()
        sources = {
            "rows": ledger.window(now, days),
            "freeloaders": ledger.freeloaders(now, days),
        }

    return {
        "sources": sources,
        "llm": llm,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": total,
        "runs": len(rows),
        "settled": settled,
        "estimated_only": len(rows) - settled,
        "prior_total": prior_total,
        "by_profile": {k: dict(v) for k, v in sorted(by_profile.items())},
        "month_to_date": state.spent_month(),
    }


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _delta(total: float, prior: float) -> str:
    """Compare in dollars, not percent.

    A percentage against a near-zero base manufactures alarm: three cents to a
    dollar sixteen is "up 3631%", which reads like a crisis and is not one. The
    absolute prior figure is the honest comparison at these amounts.
    """
    if prior == 0 and total == 0:
        return "no spend in either week"
    if prior == 0:
        return "first week with any spend"
    direction = "up" if total >= prior else "down"
    return f"{direction} from ${prior:.2f} the week before"


def render(report: dict[str, Any], *, caps: dict[str, float]) -> tuple[str, str, str]:
    total = report["total"]
    subject = f"Lozatron spend | ${total + (report.get('llm') or {}).get('estimated_usd', 0):.2f} for the week to {report['end']}"

    lines = [
        f"Lozatron Apify spend, {report['start']} to {report['end']}",
        "",
        f"  Total            ${total:.2f}  ({_delta(total, report['prior_total'])})",
        f"  Runs             {report['runs']}",
        f"  Month to date    ${report['month_to_date']:.2f} against a ${caps['monthly']:.2f} cap",
        "",
    ]
    if report["runs"] == 0:
        lines += ["No paid runs this week. Paid sources fire at most once a day,",
                  "on the morning brief, and only when the free feeds come up short.", ""]
    else:
        for name, bucket in report["by_profile"].items():
            lines.append(f"  {name:<20} {_plural(bucket['runs'], 'run'):<8} ${bucket['usd']:.2f}"
                         + (f"  ({bucket['failed']} failed)" if bucket["failed"] else ""))
        lines.append("")
    llm = report.get("llm") or {}
    if llm.get("calls"):
        lines += [
            "  Analyst",
            f"    {_plural(llm['calls'], 'call'):<10} "
            f"{llm['input_tokens']:,} in / {llm['output_tokens']:,} out tokens",
            f"    ~${llm['estimated_usd']:.2f} estimated at the configured token rate",
            "",
        ]
    if report["estimated_only"]:
        lines += [f"  {_plural(report['estimated_only'], 'run')} not yet settled by Apify;",
                  "  the reservation is counted instead, so the figure may move.", ""]

    rows_html = "".join(
        f'<tr><td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{INK};">{html.escape(name)}</td>'
        f'<td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{MUTED};text-align:right;">'
        f'{_plural(bucket["runs"], "run")}</td>'
        f'<td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{INK};text-align:right;">'
        f'${bucket["usd"]:.2f}</td></tr>'
        for name, bucket in report["by_profile"].items()
    ) or (
        f'<tr><td colspan="3" style="padding:6px 0;font-size:14px;color:{MUTED};">'
        f'No paid runs this week.</td></tr>'
    )

    llm_html = ""
    if llm.get("calls"):
        llm_html = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-bottom:1px solid {RULE};margin:0 0 20px;"><tbody>'
            f'<tr><td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{INK};">analyst</td>'
            f'<td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{MUTED};text-align:right;">'
            f'{_plural(llm["calls"], "call")}</td>'
            f'<td style="padding:6px 0;font-family:{MONO};font-size:13px;color:{INK};text-align:right;">'
            f'~${llm["estimated_usd"]:.2f}</td></tr>'
            f'<tr><td colspan="3" style="padding:0 0 8px;font-family:{MONO};font-size:11px;color:{MUTED};">'
            f'{llm["input_tokens"]:,} in / {llm["output_tokens"]:,} out tokens measured; '
            f'dollars estimated at the configured rate</td></tr>'
            f'</tbody></table>'
        )

    sources = report.get("sources") or {}
    rows_by_source = sources.get("rows") or {}
    source_html = ""
    if rows_by_source:
        ranked = sorted(rows_by_source.items(),
                        key=lambda kv: (-kv[1]["delivered"], -kv[1]["eligible"], kv[0]))
        lines += ["Source yield this week (items / cleared the gate / delivered)", ""]
        for name, counts in ranked:
            lines.append(f"  {name[:26]:26} {counts['items']:5} {counts['eligible']:5} {counts['delivered']:5}")
        lines.append("")
        if sources.get("freeloaders"):
            lines += ["Fetched all week and delivered nothing:",
                      "  " + ", ".join(sources["freeloaders"]),
                      "  Worth a look before the list grows again.", ""]
        source_rows = "".join(
            f'<tr><td style="padding:5px 0;font-family:{MONO};font-size:12px;color:{INK};">'
            f'{html.escape(name)}</td>'
            f'<td style="padding:5px 0;font-family:{MONO};font-size:12px;color:{MUTED};text-align:right;">'
            f'{counts["items"]}</td>'
            f'<td style="padding:5px 0;font-family:{MONO};font-size:12px;color:{MUTED};text-align:right;">'
            f'{counts["eligible"]}</td>'
            f'<td style="padding:5px 0;font-family:{MONO};font-size:12px;color:{INK};text-align:right;">'
            f'{counts["delivered"]}</td></tr>'
            for name, counts in ranked
        )
        freeloader_html = ""
        if sources.get("freeloaders"):
            freeloader_html = (
                f'<p style="margin:10px 0 0;font-family:{MONO};font-size:11px;'
                f'line-height:1.6;color:{MUTED};">Fetched all week, delivered nothing: '
                f'{html.escape(", ".join(sources["freeloaders"]))}</p>'
            )
        source_html = (
            f'<p style="margin:0 0 6px;font-family:{MONO};font-size:12px;letter-spacing:.06em;'
            f'text-transform:uppercase;color:{MUTED};">Source yield</p>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-top:1px solid {RULE};border-bottom:1px solid {RULE};margin:0 0 6px;">'
            f'<tbody>{source_rows}</tbody></table>'
            f'<p style="margin:0;font-family:{MONO};font-size:11px;color:{MUTED};">'
            f'items fetched &middot; cleared the gate &middot; delivered</p>'
            f'{freeloader_html}'
            f'<div style="height:22px;"></div>'
        )

    caveat = ""
    if report["estimated_only"]:
        caveat = (
            f'<p style="margin:14px 0 0;font-family:{MONO};font-size:12px;color:{FLAG};">'
            f'{report["estimated_only"]} run(s) not yet settled by Apify; reservation counted, '
            f'figure may move.</p>'
        )

    html_body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f'<title>{html.escape(subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:{PAPER};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{PAPER};"><tbody><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:520px;width:100%;"><tbody><tr>'
        f'<td style="padding:36px 30px;font-family:{SANS};">'
        f'<h1 style="margin:0 0 4px;font-size:15px;font-weight:600;color:{INK};">Apify spend</h1>'
        f'<p style="margin:0 0 26px;font-family:{MONO};font-size:12px;color:{MUTED};">'
        f'{report["start"]} to {report["end"]}</p>'
        f'<p style="margin:0 0 4px;font-size:34px;font-weight:600;color:{INK};">${total:.2f}</p>'
        f'<p style="margin:0 0 26px;font-size:14px;color:{MUTED};">{html.escape(_delta(total, report["prior_total"]))}</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-top:1px solid {RULE};border-bottom:1px solid {RULE};margin:0 0 20px;">'
        f'<tbody>{rows_html}</tbody></table>'
        f'{llm_html}'
        f'{source_html}'
        f'<p style="margin:0;font-family:{MONO};font-size:12px;line-height:1.7;color:{MUTED};">'
        f'Month to date ${report["month_to_date"]:.2f} of ${caps["monthly"]:.2f}<br>'
        f'Daily cap ${caps["daily"]:.2f} &middot; hard per-run ceiling enforced by Apify<br>'
        f'Paid sources run at most once a day, morning brief only</p>'
        f'{caveat}'
        '</td></tr></tbody></table></td></tr></tbody></table></body></html>'
    )
    return subject, "\n".join(lines), html_body
