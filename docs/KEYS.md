# LLM keys and delivery guard

Status: Current
Last verified: 2026-10-21

## Change a key (the only way)

1. GitHub: this repo > Settings > Secrets and variables > Actions. Update `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`.
2. Actions > **Sync LLM keys to VPS** > Run workflow.
3. Read the run summary. Each provider shows `installed`, `unchanged` or `REFUSED, kept previous key`.

The VPS tests every key with a real one-token call before installing it. A key that fails is refused and the working key stays, so a bad paste cannot take the agents down. An empty secret is skipped, never deleted. The sync also runs every Monday, which proves the path works before the day you need it.

Use **API keys** (console.anthropic.com, platform.openai.com, platform.deepseek.com), not a Claude subscription login. Outside tools appear to bill a subscription login to its "extra usage" (that is what the 2026-10-15 error says), which is how the brief went quiet on Sonnet while the subscription still had allowance left.

## Where the keys live

| Place | What it holds |
|---|---|
| GitHub secrets (this repo) | The copy you edit. Write-only: nobody can read it back. |
| VPS `/root/.openclaw/secrets/llm-keys.json` | The copy every OpenClaw agent reads, root-only (0600). The last 5 versions are kept beside it. |
| Every agent's `auth-profiles.json` | No keys. `anthropic:api`, `openai:api` and `deepseek:api` are SecretRefs pointing at the file above. |

The older per-agent copies (`openai:default`, the `anthropic:default` subscription token, the DeepSeek key in `models.json`) are still on the box as a last resort. They come after the `:api` profiles in `auth.order`.

## Why the brief should not miss again

| Layer | When (London) | What it does |
|---|---|---|
| Model chain | 15:00 brief | Sonnet (Anthropic API) > gpt-5.3-codex (OpenAI) > deepseek-v4-pro. Three separate billing accounts. |
| Preflight | 14:30 | Tests all three keys, checks that every cron model is on the allowlist (the cause of the September outage) and that the gateway and the brief job are up. Telegrams Krish if anything is wrong. |
| Postcheck | 15:45 | Looks for Telegram's own `ok` for today's send to Lauren. If it isn't there, triggers one rerun. |
| Postcheck | 16:45 | Still missing: Telegrams Krish. |
| GitHub watchdog | hourly 15:23 to 21:23 UTC | Asks the VPS for its status. No answer, or the gateway is down: Telegrams Krish, because then the VPS can't. The brief was missed: the run fails, so GitHub emails as well. |

Alerts reach Krish through `@agathaops_bot` ("Arlo" in his Telegram), at most one per kind per day. No LLM sits in any guard path.

## Moving parts

- `ops/vps/openclaw-keyctl`: installed at `/usr/local/sbin/openclaw-keyctl`. Subcommands `apply`, `status`, `test`, `preflight`, `postcheck`.
- `ops/vps/configure_openclaw.py`: points agents at the key file and sets Lozatron's model chain. It is idempotent and backs up every file it changes as `*.bak-keyctl-<stamp>`.
- `ops/vps/install.sh`: installs both, creates the `keysync` user and its root cron lines. Re-run it after changing any of these files.
- `keysync` SSH user: its key lives in the `VPS_KEYSYNC_SSH_KEY` secret. A forced command plus a sudoers rule mean it can run only `openclaw-keyctl apply` or `status`, never a shell.
- Secrets the workflows need: `VPS_HOST`, `VPS_KNOWN_HOSTS` (pinned ed25519 host key), `VPS_KEYSYNC_SSH_KEY`, `ALERT_BOT_TOKEN`, `ALERT_CHAT_ID`, plus the three API keys.

## Never

- Add an `api` profile for a provider that has no key in `llm-keys.json`. OpenClaw refuses to start when a SecretRef can't be resolved, and `configure_openclaw.py` only wires providers that have a key for this reason.
- Point a cron job at a model that is not in `agents.defaults.models`. The preflight catches it, but the job dies before it runs.
