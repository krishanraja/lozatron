# VPS retirement, 2026-09-20

## Result

Lozatron no longer executes or receives Telegram traffic on the OpenClaw VPS. GitHub Actions owns briefing execution and Gmail owns delivery.

## Gateway jobs disabled

| Job | Enabled after readback |
|---|---:|
| loz-api-monitor | false |
| loz-memory-maintenance | false |
| loz-news-briefing-9am | false |
| loz-cultural-collab-radar | false |
| loz-news-briefing-2pm | false |
| loz-news-briefing-6pm | false |
| loz-breaking-news-monitor | false |

## Root cron removed

The five lines invoking these VPS scripts were removed from the live root crontab:

- `fix_orphaned_briefings.py`
- `loz_freshness_diagnostic.py`
- `loz_signal_radar.py`
- `verify_doc.py`
- `briefing_and_send.py`

## Live OpenClaw configuration

- Agent `loz` removed from the live agent list.
- The Loz Telegram binding removed.
- Telegram account `loz` removed.
- OpenClaw configuration validated.
- Gateway restarted and read back as active.
- No Lozatron process remained after restart.

## Backups

- `/root/.openclaw/cron/jobs.json.bak-lozatron-retire-20260920-1725`
- `/root/.openclaw/openclaw.json.bak-lozatron-retire-20260920-1725`
- `/root/.openclaw/cron/root-crontab.bak-lozatron-retire-20260920-1725`

## Cold evidence retained

The workspace, agent directory, memory database, logs, caches, credentials and Telegram credential files were not deleted. They cannot execute through the retired schedules or live agent configuration. They remain available for rollback or a later separately approved deletion.

## Rollback

Restore the three backup files above, validate the OpenClaw configuration, restart the user-level `openclaw-gateway.service`, and read back the gateway jobs, root crontab, agent list, binding and Telegram account. Before rollback, set the GitHub repository variable `LOZATRON_ENABLED=false` to prevent duplicate delivery.

