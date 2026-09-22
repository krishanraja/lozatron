# Lozatron

Lozatron is an email-first creator-business briefing service for Lauren. GitHub Actions owns the schedules and runtime. The retired OpenClaw VPS is not part of the production path.

Production status: live on GitHub Actions since 2026-09-20. Telegram delivery is retired.

## Who this is for

Lauren is President of The Publish Press. She publishes creator-economy news
for a living, so a brief that re-reports it is a worse version of her own
product. What this exists to give her is the half she cannot get from her own
newsroom: the business events that matter to her, and the **mechanics**
underneath them -- how a media business actually works, and what is worth
borrowing.

Everything below follows from that, and from
`lauren-feedback-rules.md`, which is the authority on what ships.

## What runs

**One brief a day, 09:00 `America/New_York`.** Nothing else. Three slots plus
sixteen breaking checks was an assumption nobody made, and it produced a flood
of low-quality mail; breaking-news is retired. The slot ledger means a delayed
or repeated run still yields exactly one email, and a fail-closed ceiling in
`state/delivered.json` caps the day at two whatever else is misconfigured.

**Two tracks, which never compete.**

- *News* -- 17 trade feeds plus seven NewsAPI searches, 48-hour window, at most
  ten stories, two per outlet. Rule 4 governs it: deals, funding, platform
  economics, monetization, agencies, regulation with commercial impact. It
  rejects explainers and commentary, and that has not been relaxed.
- *Mechanics* -- five media-business publications (Nieman Lab, Press Gazette,
  A Media Operator, Digital Content Next, Simon Owens), seven-day window,
  capped at two, one per outlet, omitted entirely when nothing clears. These
  publish weekly, so a news-shaped freshness window would never surface them.
  It has its own gate: a media-business vocabulary rather than a creator one,
  because these say "publishers" and "monetize", never "creator".

Mechanics never counts toward the empty-brief check. A day with no
creator-business news is a quiet day, and last Tuesday's essay is not a reason
to send anyway.

**Social is commentary, never a story.** A post is one person's opinion; it is
not reporting. Social is excluded from clustering outright, so it cannot become
a numbered entry even by accident. It reaches the brief two ways, both
aggregate: attached to a story as a count of who is discussing it, without
changing that story's rank; or, when three or more *distinct* accounts converge
on a theme with nothing reported behind it, as one line under "Being discussed
· unconfirmed". Distinct accounts, not posts -- one person posting six times is
one person, which is how a single thread becomes a "trend".

**The mandate gate reads the article, not the publisher's footer.** Feeds
append "follow us on Instagram and YouTube · subscribe to our newsletter ·
we're hiring" to every item, and matching a creator term anywhere in that text
let general-interest feeds pass on their own chrome: measured live, all three
Axios stories that cleared the old gate were political. The creator term must
now be in the headline, and business terms are matched against the first 400
characters of the summary.

**Reddit and YouTube channel RSS were removed on evidence.** Reddit supplied
125 of 373 pool items and nothing it surfaced was ever corroborated, which is
the bar Lauren's own rule sets. YouTube channel RSS returns the last fifteen
uploads regardless of age; the one item that reached her was a sponsored post.

Everything else holds: deterministic freshness, mandate and duplicate gates;
clustering, corroboration and the per-outlet cap unconditional in code with no
flag able to switch them off; Gmail delivery only after tests and credential
verification pass; delivery state committed only after Gmail returns a message
id.

## Which sources earn their place

`state/source_yield.json` records, per source per day, how many items were
fetched, how many were fresh, how many cleared the gate and how many were
delivered. The weekly cost email reports the table and names any source
fetched all week that delivered nothing.

This exists because the question had no answer that did not involve a human
re-running measurements by hand -- which is how Kajabi sat in the list
contributing nothing, and how a feed serving a leading newline before its XML
declaration hid thirty live items behind the single word `ParseError` in a
swallowed error list. Counts only: no titles, no URLs, nothing about what
Lauren read.

## The kill switch

`READER_DELIVERY_ENABLED` in `src/lozatron/gmail.py` decides whether the brief
reaches the reader. Set it to `False` and `recipients()` returns the ops
address and only the ops address, whatever `LOZ_RECIPIENT_EMAILS` says; the
run still happens, still renders, still records state, and the reader receives
nothing.

It is a code constant rather than a repository variable on purpose. On
2026-09-22 the reader was flooded because a safety rule lived in a workflow
variable that had been set on one of the two workflows that needed it, so the
other kept running the ungated path. A constant cannot be absent, cannot be
misspelled in one of two YAML files, and appears in the diff when it changes.

`tests/test_hard_stop.py` sets it both ways rather than relying on whichever
value ships today, so the mechanism stays proven in whichever state it is
currently in.

Backstopping it: `DeliveryState.MAX_SENDS_PER_DAY` caps the day at two emails,
counted in the committed ledger and checked immediately before `gmail.send`.
It bounds every path at once, so no future gate or flag can produce a flood
whatever else is misconfigured.

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

**When money may be spent.** At most one paid opportunity a day, on the morning
brief. The profiles no longer serve one purpose, so one answer cannot cover
both. The *social* profiles supply the commentary layer, so they skip the
free-pool test entirely: their job is to say what is being discussed around the
stories we already have, which is most useful on the days there are stories --
exactly the days the old gate skipped them. The *story* profiles keep the
original bargain and run only when the free feeds came up short
(`LOZ_PAID_MIN_FREE`, default 10; it moved up from 6 because widening the free
list raises the count that suppresses them, so otherwise every feed added
quietly retires the paid layer).

Paid scraping previously fired on every non-dry run with no mode check, so the
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
