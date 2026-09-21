# Lozatron

Lozatron is an email-first creator-business briefing service for Lauren. GitHub Actions owns the schedules and runtime. The retired OpenClaw VPS is not part of the production path.

Production status: live on GitHub Actions since 2026-09-20. Telegram delivery is retired.

## What runs

- Briefings at 09:00, 14:00 and 18:00 in `America/New_York`.
- Breaking-news checks every 90 minutes.
- RSS discovery by default, with NewsAPI and guarded Apify sources when enabled.
- Deterministic freshness, mandate and duplicate gates.
- Gmail delivery only after tests and credential verification pass.
- Delivery state committed only after Gmail returns a message id.

## Delivery slots

GitHub's scheduler is best-effort: observed runs on this repository arrive
between 22 minutes and 2.6 hours after their nominal time, and some slots are
dropped outright. Scheduled briefings therefore do not ask "what hour is it?" —
they ask which Eastern slot is still outstanding, within a 200-minute grace
window, and deliver that. A delayed run still delivers. A slot already recorded
in `state/delivered.json` cannot fire twice. A slot missed beyond grace stays
missed rather than arriving in the evening dressed as the morning brief.

`workflow_dispatch` always bypasses the gate, so a human can force a delivery.
`LOZ_BRIEF_SLOTS_ET` overrides the default `9,14,18`.

Each edition carries a deterministic `Message-ID`. The Gmail send is the one
call that is never retried on a timeout or server error, because either may
mean the message was already accepted; on an ambiguous failure the edition id is
probed instead, so a resend cannot put the brief in the inbox twice.

Scheduled jobs stay off until the repository variable `LOZATRON_ENABLED` is set to `true`.

## The analysis layer

Off by default. `LOZ_ANALYST` takes `off`, `shadow` or `live`.

The model reasons; code decides. It is sent titles, outlets, summaries and an
integer age, never a URL and never a timestamp, so it cannot choose a link or
invent a date. It returns JSON against a strict schema, and a second validation
pass rejects any individual story containing markup, a link, a line break, an
unknown id, or a field that merely echoes its input. It does not choose what
ships, how many ship, or in what order: deterministic gates and `rank()` own
all three.

Every failure degrades to the deterministic brief and names its reason in the
step summary: `auth_failed`, `quota_exhausted`, `model_not_found`,
`rate_limited`, `schema_rejected`, `render_shape_failed`. A retired model id
moves to the next entry in `LOZ_ANALYST_MODELS` rather than taking the run down.
Spend is reserved before the call and capped by `LOZ_LLM_DAILY_USD_CAP`.

Breaking-news runs are always deterministic. They exist to move fast, and the
model would add sixteen daily chances for the critical path to fail.

## The archive

`LOZ_ARCHIVE=true` writes each edition to the `lozatron` Postgres schema, which
is deliberately not part of the mind/make OS and grants nothing to `anon` or
`authenticated`. An anon key on that project reaches OS tables, so no browser
may ever hold one: any future brief page must read through a server-side route
holding the service key.

The schema must be listed under Settings > API > Exposed schemas. Until it is,
archiving reports `schema_not_exposed` and the brief ships unaffected.

## Required repository secrets

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_SENDER_EMAIL`
- `LOZ_RECIPIENT_EMAILS`, comma-separated
- `LOZ_CC_EMAILS`, optional comma-separated CC recipients

Optional:

- `NEWSAPI_KEY`
- `APIFY_TOKEN`
- `OPENAI_API_KEY`, required only when `LOZ_ANALYST` is not `off`
- `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, required only when `LOZ_ARCHIVE=true`

## Paid sources and cost control

Disabled by default, and gated four ways even when enabled.

**When money may be spent.** At most one paid opportunity a day: the morning
brief only, and only when the free feeds came up short (`LOZ_PAID_MIN_FREE`,
default 6). Breaking-news runs never spend. This matters more than any cap:
paid scraping previously fired on every non-dry run with no mode check, so the
sixteen breaking checks a day could consume the whole budget overnight, before
the 9am brief that is actually read.

**How much may be spent.** Three independent fail-closed gates: runs per profile
per day, `LOZ_APIFY_DAILY_USD_CAP` (default 1.00) and
`LOZ_APIFY_MONTHLY_USD_CAP` (default 8.00). Above those, every run carries
`maxTotalChargeUsd` and `maxItems`, which Apify enforces on its own side. The
local ledger can only decide whether to start a run; it can never decide how
much that run bills, so the hard ceiling is the one that holds when an actor
misbehaves.

**What it actually cost.** Spend is reserved before dispatch, then reconciled
against the `usageTotalUsd` Apify reports for that run. The estimates in
`PROFILES` are reservations only and have never been an invoice. A failed run
still counts: it still billed.

**Weekly report.** `.github/workflows/apify-cost.yml` emails last week's spend
every Monday at 08:00 ET, to `LOZ_OPS_EMAILS` (falling back to `LOZ_CC_EMAILS`)
and never to the brief's recipients. It reports Lozatron's own runs, not
account-wide Apify usage, which would sweep in unrelated actors and make the
figure unattributable. Run it on demand with
`lozatron --cost-report --dry-run`.

No secret value belongs in this repository, logs, issues or workflow inputs.

## Local verification

```bash
python -m pip install -e . pytest
pytest -q
lozatron --mode briefing --dry-run
```

See [docs/MIGRATION.md](docs/MIGRATION.md) for cutover and rollback.
