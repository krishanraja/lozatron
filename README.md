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

Scheduled jobs stay off until the repository variable `LOZATRON_ENABLED` is set to `true`.

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

Paid sources also require `LOZ_ENABLE_PAID_SOURCES=true`. The optional `LOZ_APIFY_DAILY_USD_CAP` variable defaults to `1.00`. Paid sources are disabled by default.

No secret value belongs in this repository, logs, issues or workflow inputs.

## Local verification

```bash
python -m pip install -e . pytest
pytest -q
lozatron --mode briefing --dry-run
```

See [docs/MIGRATION.md](docs/MIGRATION.md) for cutover and rollback.
