#!/usr/bin/env python3
"""Point every OpenClaw agent at the single key file, and give Lozatron independent fallbacks.

Idempotent. Run as root with the gateway STOPPED (install.sh does that), because the
gateway rewrites jobs.json and config on its own schedule. Every file it touches is
backed up first as <file>.bak-keyctl-<UTC stamp>.

What it changes
1. openclaw.json
   - secrets.providers.llmkeys: a file SecretRef provider for /root/.openclaw/secrets/llm-keys.json
   - auth.profiles: anthropic:api, openai:api, deepseek:api (mode api_key)
   - auth.order: API keys first; the old subscription/token profiles stay as the last resort
2. agents/*/agent/auth-profiles.json: the three *:api profiles, each a keyRef into llm-keys.json.
   No key material is written into these files.
3. Lozatron model chain, so one dry account can never stop the brief:
   - agent loz: Sonnet (Anthropic API) -> gpt-5.3-codex (OpenAI) -> deepseek-v4-pro -> Haiku
   - brief cron payload: Sonnet -> gpt-5.3-codex -> deepseek-v4-pro
"""

import datetime as dt
import glob
import json
import os
import shutil
import sys

OPENCLAW = "/root/.openclaw"
CONFIG = f"{OPENCLAW}/openclaw.json"
JOBS = f"{OPENCLAW}/cron/jobs.json"
KEYS_FILE = f"{OPENCLAW}/secrets/llm-keys.json"
BRIEF_JOB = "427dc942-b6da-4a28-b702-247785aa60ca"
STAMP = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

API_PROFILES = {"anthropic": "anthropic:api", "openai": "openai:api", "deepseek": "deepseek:api"}
ORDER = {
    "anthropic": ["anthropic:api", "anthropic:default"],
    "openai": ["openai:api", "openai:default"],
    "deepseek": ["deepseek:api"],
}
LOZ_MODEL = {"primary": "anthropic/claude-sonnet-4-6",
             "fallbacks": ["openai/gpt-5.3-codex", "deepseek/deepseek-v4-pro",
                           "anthropic/claude-haiku-4-5-20251001"]}
BRIEF_MODEL = "anthropic/claude-sonnet-4-6"
BRIEF_FALLBACKS = ["openai/gpt-5.3-codex", "deepseek/deepseek-v4-pro"]


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, data):
    shutil.copy2(path, f"{path}.bak-keyctl-{STAMP}")
    tmp = f"{path}.tmp-keyctl"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)


def keyref(provider):
    return {"source": "file", "provider": "llmkeys", "id": f"/{provider}"}


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(KEYS_FILE):
        sys.exit(f"{KEYS_FILE} must exist (with at least one key) before refs can point at it")
    keys = load(KEYS_FILE)
    present = [p for p in API_PROFILES if keys.get(p)]
    changes = []

    # 1. openclaw.json
    cfg = load(CONFIG)
    sec = cfg.setdefault("secrets", {})
    prov = sec.setdefault("providers", {})
    want = {"source": "file", "path": KEYS_FILE, "mode": "json"}
    if prov.get("llmkeys") != want:
        prov["llmkeys"] = want
        changes.append("config: secrets.providers.llmkeys")
    auth = cfg.setdefault("auth", {})
    profiles = auth.setdefault("profiles", {})
    for p in present:
        pid = API_PROFILES[p]
        if profiles.get(pid) != {"provider": p, "mode": "api_key"}:
            profiles[pid] = {"provider": p, "mode": "api_key"}
            changes.append(f"config: auth.profiles.{pid}")
    order = auth.setdefault("order", {})
    for p in present:
        o = [pid for pid in ORDER[p] if pid.endswith(":api") or pid in profiles]
        if order.get(p) != o:
            order[p] = o
            changes.append(f"config: auth.order.{p} = {o}")
    for a in cfg.get("agents", {}).get("list", []):
        if a.get("id") == "loz" and a.get("model") != LOZ_MODEL:
            a["model"] = LOZ_MODEL
            changes.append("config: agent loz model chain")
    if changes:
        save(CONFIG, cfg)

    # 2. per-agent auth profiles
    for path in sorted(glob.glob(f"{OPENCLAW}/agents/*/agent/auth-profiles.json")):
        ap = load(path)
        pr = ap.setdefault("profiles", {})
        touched = False
        for p in present:
            pid = API_PROFILES[p]
            want_p = {"type": "api_key", "provider": p, "keyRef": keyref(p)}
            if pr.get(pid) != want_p:
                pr[pid] = want_p
                touched = True
        if touched:
            save(path, ap)
            changes.append(f"profiles: {path.split('/agents/')[1].split('/')[0]}")

    # 3. brief cron payload
    jobs_doc = load(JOBS)
    jobs = jobs_doc["jobs"] if isinstance(jobs_doc, dict) else jobs_doc
    for j in jobs:
        if j.get("id") == BRIEF_JOB:
            pl = j.setdefault("payload", {})
            if pl.get("model") != BRIEF_MODEL or pl.get("fallbacks") != BRIEF_FALLBACKS:
                pl["model"], pl["fallbacks"] = BRIEF_MODEL, BRIEF_FALLBACKS
                save(JOBS, jobs_doc)
                changes.append("cron: brief model chain")
            break

    print(json.dumps({"keys_present": present, "changes": changes or ["none (already configured)"]}, indent=2))


if __name__ == "__main__":
    main()
