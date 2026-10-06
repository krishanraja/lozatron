"""Brief archive, held in a siloed Postgres schema.

The archive is the substrate for three things that cannot exist without a
history: novelty scoring (what has Lauren already been told), delta since the
last brief, and any future backtest of a ranking change.

It lives in the `lozatron` schema, never `public`. That schema grants nothing to
`anon` or `authenticated`, because an anon key on this project reaches the
mind/make OS tables and a browser must never hold one. Reaching the archive from
a page means a server-side route holding the service key, never a client call.

Writing is best effort by design. A brief that was delivered but not archived is
a lost record; a brief that was not delivered because archiving failed is a lost
brief. The second is worse, so nothing here can raise into the send path.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import http
from .brief import Brief

SCHEMA = "lozatron"


class MissingCredentials(RuntimeError):
    """No Supabase credentials in the environment.

    Its own type because the learning loop must tell two situations apart. A
    quiet week with no taps is normal and silent. Credentials that were never
    wired is a misconfiguration that would otherwise look identical -- the
    loop reads nothing, reports success, and appears to work for weeks while
    learning nothing at all. The first version of learn.yml did exactly that,
    because no workflow in this repo had ever passed SUPABASE_*. So this
    raises, and `app.learn` turns it into a failed run.
    """


def configured() -> bool:
    from .core import env_flag, env_text
    return bool(env_text("SUPABASE_URL") and env_text("SUPABASE_SERVICE_ROLE_KEY")
                and env_flag("LOZ_ARCHIVE"))


def _headers(write: bool) -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    # PostgREST addresses a non-default schema by profile header. The schema
    # must also be listed under Settings > API > Exposed schemas, or this
    # returns PGRST106 and the run degrades rather than failing.
    headers["Content-Profile" if write else "Accept-Profile"] = SCHEMA
    return headers


def _post(table: str, rows: list[dict[str, Any]]) -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    http.request(
        f"{base}/rest/v1/{table}",
        method="POST",
        data=json.dumps(rows).encode("utf-8"),
        headers=_headers(write=True),
        timeout=30,
        retries=2,
    )


def record(brief: Brief, *, edition_id: str, sent: bool, model: str = "",
           counts: dict[str, int] | None = None) -> str:
    """Archive one edition. Returns 'ok' or a named reason. Never raises."""
    if not configured():
        return "archive_disabled"
    try:
        _post("briefs", [{
            "edition_id": edition_id,
            "mode": brief.mode,
            "slot_et": brief.slot,
            "generated_at": brief.generated_at.isoformat(),
            "sent": sent,
            "degraded": brief.degraded,
            "analyst_model": model or None,
            "lede": brief.lede,
            "counts": counts or brief.filtered,
        }])
        if brief.entries:
            _post("entries", [{
                "edition_id": edition_id,
                "rank": index,
                "cluster_key": "",
                "title": entry.title,
                "url": entry.url,
                "outlets": entry.outlets,
                "corroboration": entry.corroboration,
                "age_hours": entry.age_hours,
                "what_happened": entry.what_happened,
                "why_it_matters": entry.why_it_matters,
                "what_to_watch": entry.what_to_watch,
                "needs_decision": entry.needs_decision,
                "analysed": entry.analysed,
            } for index, entry in enumerate(brief.entries, 1)])
        return "ok"
    except Exception as exc:  # noqa: BLE001 - archiving never breaks delivery
        detail = str(exc)
        if "PGRST106" in detail or "Invalid schema" in detail:
            return "schema_not_exposed"
        return f"archive_failed:{type(exc).__name__}"


def recent(days: int = 14, limit: int = 60) -> list[dict[str, Any]]:
    """Headlines already delivered, for novelty context. Empty on any failure."""
    if not configured():
        return []
    base = os.environ["SUPABASE_URL"].rstrip("/")
    query = f"select=title,edition_id&order=id.desc&limit={limit}"
    try:
        rows = http.request_json(
            f"{base}/rest/v1/entries?{query}",
            headers=_headers(write=False),
            timeout=20,
            retries=1,
        )
        return [{"title": row.get("title", ""), "date": row.get("edition_id", "")[:10]}
                for row in rows if isinstance(row, dict)]
    except Exception:  # noqa: BLE001
        return []


def signals(days: int = 30) -> list[dict[str, Any]]:
    """Lauren's button feedback, for the learning loop. Empty on any failure.

    Read through `public.loz_recent_signals`, a SECURITY DEFINER function,
    rather than over REST: the `lozatron` schema is deliberately not exposed,
    because widening this project's exposed schema list would put a private
    schema on a public surface, and an anon key here reaches mind/make OS
    tables. The write path uses the same pattern.

    The function returns no notes and no user agent. The loop needs the verdict
    and the headline it was cast on; her free-text notes are hers, and this
    runs in GitHub Actions where anything read risks reaching a public repo's
    run log.
    """
    from .core import env_text

    # Credentials only, deliberately not `configured()`. That helper also
    # requires LOZ_ARCHIVE, which gates whether briefs are archived -- a
    # separate decision from whether Lauren's feedback is read. Reusing it here
    # made the loop silently read nothing, which is how this was found.
    key = env_text("SUPABASE_SERVICE_ROLE_KEY")
    base = env_text("SUPABASE_URL").rstrip("/")
    if not (key and base):
        raise MissingCredentials(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required to read "
            "Lauren's feedback"
        )
    try:
        rows = http.request_json(
            f"{base}/rest/v1/rpc/loz_recent_signals",
            method="POST",
            data=json.dumps({"p_days": int(days)}).encode(),
            # Plain headers, with no Accept-Profile. `_headers` points
            # PostgREST at the `lozatron` schema, which is right for the
            # archive tables and wrong here: this RPC lives in `public`,
            # precisely so the private schema stays unexposed. Sending the
            # profile header asked for a function that is not there.
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=20,
            retries=1,
        )
    except Exception:  # noqa: BLE001 - learning never breaks delivery
        return []
    if not isinstance(rows, list):
        return []
    return [
        {
            "action": str(row.get("action", "")),
            "story_title": str(row.get("story_title", "") or ""),
            "cluster_key": row.get("cluster_key"),
        }
        for row in rows
        if isinstance(row, dict)
    ]
