"""Deterministic rendering. The model never produces a byte of this.

Every string that came from a feed or from the model is escaped before it
reaches the markup, and the markup itself is assembled from literal fragments.
That is the contract that closed the September 2026 formatting collapse, where
the model emitted nested bold bullets and the renderer reproduced them
faithfully. `assert_render_shape` runs in production, not only in tests.

State of use: a phone, early morning, between meetings, perhaps thirty seconds
of attention. So the verdict sits on each story rather than in a summary at the
foot, and nothing requires a scroll to find out what a story means.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .brief import Brief, Entry
from .core import clean_feed_text

EASTERN = ZoneInfo("America/New_York")

INK = "#14171a"
PAPER = "#faf8f5"
MUTED = "#6b7075"
RULE = "#e4e0da"
ANSWER = "#1f6f5c"     # why it matters: the thing she is reading for
LINK = "#1b5e8c"       # only ever used for something you can click
FLAG = "#8a5a12"       # needs a decision, or low confidence
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

ALLOWED_TAGS = frozenset({
    "html", "head", "meta", "title", "style", "body", "div", "table", "tbody",
    "tr", "td", "h1", "h2", "h3", "p", "a", "span", "strong", "em", "hr",
})


class _Shape(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []
        self.bad_attrs: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, _ in attrs:
            if name.lower().startswith("on"):
                self.bad_attrs.append(f"{tag}[{name}]")


def assert_render_shape(markup: str, entry_count: int) -> None:
    """Structural check, run before every send.

    The tag whitelist catches injected markup. The heading count catches the
    specific September failure shape, where distinct stories collapsed into one
    undifferentiated block, which a whitelist alone would happily pass.
    """
    parser = _Shape()
    parser.feed(markup)
    unexpected = sorted(set(parser.tags) - ALLOWED_TAGS)
    if unexpected:
        raise ValueError(f"disallowed tags in rendered email: {unexpected}")
    if parser.bad_attrs:
        raise ValueError(f"event handler attributes present: {parser.bad_attrs}")
    headings = parser.tags.count("h2")
    if headings != entry_count:
        raise ValueError(f"expected {entry_count} story headings, found {headings}")


def _t(value: str, limit: int = 600) -> str:
    """Escape trusted plain text once.

    Used for analyst prose and our own strings, which are plain by contract.
    Deliberately does NOT unescape: unescaping model output could resurrect
    markup the analyst's validation gate had already neutralised, so the feed
    cleaner is kept to the one place that needs it.
    """
    return html.escape(re.sub(r"\s+", " ", str(value or "")).strip()[:limit])


def _feed(value: str, limit: int = 400) -> str:
    """Escape a raw feed summary, after unescaping the entities it arrives with.

    Feed descriptions carry `&#8217;` and friends. Escaping those directly makes
    the `&` into `&amp;`, so the entity prints literally in the inbox. Unescape,
    strip tags, then escape exactly once.
    """
    return html.escape(clean_feed_text(value, limit))


def _age(hours: int) -> str:
    if hours < 1:
        return "just now"
    if hours == 1:
        return "1 hour ago"
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    return "yesterday" if days == 1 else f"{days} days ago"


def _heading(local: dt.datetime, mode: str, slot: str | None) -> str:
    if mode == "breaking":
        return "Breaking"
    hour = int(slot.split("T")[1]) if slot and "T" in slot else local.hour
    return {9: "Morning brief", 14: "Afternoon brief", 18: "Evening brief"}.get(hour, "Brief")


def _clip(value: str, limit: int) -> str:
    """One-line form with an ellipsis, so an index cannot read as a repeat."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "\u2026"


def _count(brief: Brief) -> str:
    total = len(brief.entries)
    return "1 story" if total == 1 else f"{total} stories"


def subject(brief: Brief) -> str:
    local = brief.generated_at.astimezone(EASTERN)
    return f"Lozatron | {_heading(local, brief.mode, brief.slot)} | {local:%a %-d %b}"


# --- plain text twin -------------------------------------------------------

def render_text(brief: Brief) -> str:
    local = brief.generated_at.astimezone(EASTERN)
    lines = [f"{_heading(local, brief.mode, brief.slot)}  {local:%A %-d %B, %-I:%M%p} ET", ""]
    if brief.degraded:
        lines += [f"[Analysis unavailable for this edition: {brief.degraded}]", ""]
    if brief.lede:
        lines += [brief.lede, ""]
    if brief.decisions:
        # An index pointing down at the numbered stories, matching the HTML.
        # It used to repeat each story's "why it matters" in full, so the top
        # of the email was the brief twice over.
        positions = {id(entry): number for number, entry in enumerate(brief.entries, 1)}
        lines += ["NEEDS YOUR CALL", ""]
        for entry in brief.decisions:
            lines.append(f"  {positions[id(entry)]:02d}  {entry.title}")
        lines.append("")
    for index, entry in enumerate(brief.entries, 1):
        lines.append(f"{index}. {entry.title}")
        corroboration = f" ({entry.corroboration} outlets)" if entry.corroboration > 3 else ""
        catch_up = " [catch-up]" if entry.freshness == "fallback" else ""
        lines.append(f"   {entry.outlet_line}{corroboration}, {_age(entry.age_hours)}{catch_up}")
        if entry.analysed:
            lines += [f"   What happened: {entry.what_happened}",
                      f"   Why it matters: {entry.why_it_matters}",
                      f"   What to watch: {entry.what_to_watch}"]
            if entry.low_confidence:
                lines.append("   [low confidence]")
        else:
            lines.append(f"   {clean_feed_text(entry.summary, 400)}")
        lines += [f"   {entry.url}", ""]
    if brief.filtered:
        summary = ", ".join(f"{count} {reason}" for reason, count in sorted(brief.filtered.items()))
        lines += [f"Filtered: {summary}.", ""]
    return "\n".join(lines)


# --- html ------------------------------------------------------------------

def _story(entry: Entry, index: int) -> str:
    # The corroboration count only earns its place once the outlet line has
    # collapsed into "and N more"; naming two outlets and then saying "2
    # outlets" is the same fact twice, and it costs a wrapped line on a phone.
    meta = _t(entry.outlet_line)
    if entry.corroboration > 3:
        meta += f" &middot; {entry.corroboration} outlets"
    meta += f" &middot; {_t(_age(entry.age_hours))}"
    # Her rule asks for 24-48h items to be labelled rather than silently mixed
    # in with the morning's news. Only the oldest tier is marked: labelling the
    # other two would put a badge on every story and mean nothing.
    if entry.freshness == "fallback":
        meta += (
            f' &middot; <span style="color:{MUTED};">catch-up</span>'
        )

    body = []
    if entry.analysed:
        body.append(
            f'<p style="margin:0 0 10px;font-size:16px;line-height:1.55;color:{INK};">'
            f'{_t(entry.what_happened)}</p>'
        )
        body.append(
            f'<p style="margin:0 0 10px;font-size:16px;line-height:1.55;color:{ANSWER};">'
            f'<strong style="font-weight:600;">Why it matters.</strong> {_t(entry.why_it_matters)}</p>'
        )
        body.append(
            f'<p style="margin:0 0 4px;font-size:15px;line-height:1.55;color:{MUTED};">'
            f'<strong style="font-weight:600;">Watch for.</strong> {_t(entry.what_to_watch)}</p>'
        )
        if entry.low_confidence:
            body.append(
                f'<p style="margin:8px 0 0;font-family:{MONO};font-size:12px;color:{FLAG};">'
                f'single source, treat as unconfirmed</p>'
            )
    else:
        body.append(
            f'<p style="margin:0;font-size:16px;line-height:1.55;color:{INK};">'
            f'{_feed(entry.summary, 400)}</p>'
        )

    return (
        f'<tr><td style="padding:0 0 30px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr><td>'
        f'<p style="margin:0 0 6px;font-family:{MONO};font-size:12px;letter-spacing:.04em;'
        f'color:{MUTED};">{index:02d} &middot; {meta}</p>'
        f'<h2 style="margin:0 0 10px;font-size:19px;line-height:1.3;font-weight:600;color:{INK};">'
        f'<a href="{_t(entry.url, 900)}" style="color:{LINK};text-decoration:underline;'
        f'text-underline-offset:3px;">{_t(entry.title, 300)}</a></h2>'
        + "".join(body) +
        # A second, unmissable affordance. The headline being a link is easy to
        # miss on a phone; a named destination is not.
        f'<p style="margin:14px 0 0;font-size:14px;line-height:1.4;">'
        f'<a href="{_t(entry.url, 900)}" style="color:{LINK};text-decoration:underline;'
        f'text-underline-offset:3px;font-weight:600;">Read on {_t(entry.outlets[0] if entry.outlets else "source", 40)}'
        f' &rarr;</a></p>'
        f'</td></tr></tbody></table></td></tr>'
    )


def render_html(brief: Brief) -> str:
    local = brief.generated_at.astimezone(EASTERN)
    rows: list[str] = []

    if brief.degraded:
        rows.append(
            f'<tr><td style="padding:0 0 24px;"><p style="margin:0;padding:12px 14px;'
            f'background:#f4efe6;border-radius:4px;font-family:{MONO};font-size:13px;color:{FLAG};">'
            f'Analysis unavailable for this edition ({_t(brief.degraded, 80)}). '
            f'Stories below are unanalysed.</p></td></tr>'
        )

    if brief.lede:
        rows.append(
            f'<tr><td style="padding:0 0 30px;"><p style="margin:0;font-size:18px;line-height:1.55;'
            f'color:{INK};">{_t(brief.lede)}</p></td></tr>'
        )

    if brief.decisions:
        # An index, not a repeat. Numbered so it points down at the story rather
        # than restating it directly above itself, which read as a defect.
        positions = {id(entry): number for number, entry in enumerate(brief.entries, 1)}
        items = "".join(
            f'<p style="margin:0 0 6px;font-size:15px;line-height:1.45;color:{INK};">'
            f'<span style="font-family:{MONO};font-size:12px;color:{FLAG};">'
            f'{positions[id(entry)]:02d}</span>&nbsp;&nbsp;{_t(_clip(entry.title, 64))}</p>'
            for entry in brief.decisions
        )
        rows.append(
            f'<tr><td style="padding:0 0 30px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
            f'<td style="padding:16px 18px;background:#f7f2e8;border-radius:4px;">'
            f'<p style="margin:0 0 10px;font-family:{MONO};font-size:12px;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{FLAG};">Needs your call</p>{items}'
            f'</td></tr></tbody></table></td></tr>'
        )

    for index, entry in enumerate(brief.entries, 1):
        rows.append(_story(entry, index))

    if brief.filtered:
        summary = ", ".join(f"{count} {_t(reason, 40)}" for reason, count in sorted(brief.filtered.items()))
        rows.append(
            f'<tr><td style="padding:18px 0 0;border-top:1px solid {RULE};">'
            f'<p style="margin:0;font-family:{MONO};font-size:12px;line-height:1.6;color:{MUTED};">'
            f'Screened and set aside: {summary}.</p></td></tr>'
        )

    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f'<title>{_t(subject(brief), 200)}</title>'
        '<style>@media (max-width:600px){.wrap{padding:24px 20px !important;}}</style>'
        '</head>'
        f'<body style="margin:0;padding:0;background:{PAPER};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{PAPER};"><tbody><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:600px;width:100%;"><tbody><tr>'
        f'<td class="wrap" style="padding:40px 32px 44px;font-family:{SANS};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody>'
        # Masthead. It should read like a named publication she subscribes to,
        # not like an automated alert, so the product name sits above the
        # edition and the rule under it closes the block.
        f'<tr><td style="padding:0 0 26px;">'
        f'<p style="margin:0 0 16px;font-family:{MONO};font-size:11px;letter-spacing:.24em;'
        f'text-transform:uppercase;color:{MUTED};">Lozatron</p>'
        f'<h1 style="margin:0 0 12px;font-size:30px;line-height:1.12;font-weight:600;'
        f'letter-spacing:-.015em;color:{INK};">{_t(_heading(local, brief.mode, brief.slot))}</h1>'
        f'<p style="margin:0 0 22px;font-family:{MONO};font-size:12px;color:{MUTED};">'
        f'{local:%A %-d %B} &middot; {local:%-I:%M%p} ET &middot; {_count(brief)}</p>'
        f'<div style="height:2px;background:{INK};line-height:2px;font-size:0;">&nbsp;</div>'
        f'</td></tr>'
        + "".join(rows) +
        '</tbody></table></td></tr></tbody></table></td></tr></tbody></table></body></html>'
    )


def render(brief: Brief) -> tuple[str, str, str]:
    """Returns (subject, text, html). Raises if the shape check fails."""
    markup = render_html(brief)
    assert_render_shape(markup, len(brief.entries))
    return subject(brief), render_text(brief), markup
