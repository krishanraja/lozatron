# VPS to GitHub migration

## Scope

GitHub receives executable briefing machinery only. The public repository excludes personal memory, agent sessions, local caches, logs, Google documents, Telegram configuration, and credential values.

## Cutover status, completed 2026-09-20

1. Unit tests passed locally and in GitHub Actions.
2. Gmail credentials verified without sending.
3. A workflow-dispatch dry run screened 149 source items and retained one qualifying story.
4. A controlled live email returned a Gmail message id.
5. `LOZATRON_ENABLED=true` is set.
6. The Lozatron gateway jobs and root-cron lines are disabled on the VPS.

## GitHub setup

The required secrets and variables are configured. Future credential rotation must update the matching GitHub Secret. Run either workflow manually with `dry_run=true` after code or credential changes.

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

## Owner follow-up

1. Confirm the controlled email arrived in Lauren's inbox.
2. Leave `LOZ_ENABLE_PAID_SOURCES=false` unless paid Apify sources are needed. If enabled, the default estimated cap is USD 1.00 per day.
3. Review the first automatic briefing and breaking-news runs in the repository Actions tab.
4. After a suitable rollback window, decide whether to delete the cold VPS files and rotate or revoke the retired Telegram bot token. Neither is required for the new email path.

