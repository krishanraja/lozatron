# VPS to GitHub migration

## Scope

GitHub receives executable briefing machinery only. The public repository excludes personal memory, agent sessions, local caches, logs, Google documents, Telegram configuration, and credential values.

## Cutover gates

1. Unit tests pass locally and in GitHub Actions.
2. Gmail credentials verify without sending.
3. A workflow-dispatch dry run returns qualifying source results.
4. A controlled live email returns a Gmail message id.
5. `LOZATRON_ENABLED=true` is set.
6. Only then are the Lozatron gateway jobs and root-cron lines disabled on the VPS.

## GitHub setup

Set the required secrets listed in the README. Then run `Lozatron briefings` manually with `dry_run=true`. Review its job summary. Run it once with `dry_run=false` to verify delivery. Finally create the repository variable `LOZATRON_ENABLED=true`.

Public repositories automatically lose scheduled workflows after 60 days without repository activity. Lozatron writes delivery state after successful sends, which normally keeps the repository active. GitHub can delay scheduled jobs during high load, so delivery times are targets rather than hard real-time guarantees.

## Rollback

Set `LOZATRON_ENABLED=false` in GitHub. Restore the saved root crontab and `/root/.openclaw/cron/jobs.json` backup on the VPS, then read back both schedulers before considering rollback complete.

## Deliberately not migrated

- `/root/.openclaw/workspace-loz/MEMORY.md`
- `/root/.openclaw/agents/loz`
- OpenClaw session history and SQLite memory
- Telegram bot configuration and tokens
- local briefing archives, diagnostics, logs and caches
- raw historical delivery records

These remain cold evidence on the VPS until a separately approved deletion or archival decision.

