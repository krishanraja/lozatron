#!/usr/bin/env bash
# Install the single-key-store + Lozatron delivery guard on the OpenClaw VPS.
# Usage (as root, from this directory):  bash install.sh "<keysync ssh public key>"
# Idempotent: safe to re-run after any change to these files.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
PUBKEY="${1:-}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OC=/root/.openclaw
export XDG_RUNTIME_DIR=/run/user/0 HOME=/root

# 1. Scripts
install -o root -g root -m 0755 "$HERE/openclaw-keyctl" /usr/local/sbin/openclaw-keyctl
install -d -o root -g root -m 0755 /usr/local/lib/openclaw-keyctl
install -o root -g root -m 0644 "$HERE/configure_openclaw.py" /usr/local/lib/openclaw-keyctl/configure_openclaw.py
install -d -o root -g root -m 0700 "$OC/secrets" /var/lib/openclaw-keyctl
[ -x /usr/bin/openclaw ] || ln -sf "$(command -v openclaw)" /usr/bin/openclaw

# 2. Bootstrap the key file from the keys that already work on this box (first install only).
#    Values move file to file inside root's home; nothing is printed.
if [ ! -s "$OC/secrets/llm-keys.json" ]; then
python3 - <<'PY'
import json, os
oc = "/root/.openclaw"
keys = {}
try:
    ap = json.load(open(f"{oc}/agents/loz/agent/auth-profiles.json"))["profiles"]
    if ap.get("openai:default", {}).get("key"):
        keys["openai"] = ap["openai:default"]["key"]
except Exception:
    pass
try:
    mj = json.load(open(f"{oc}/agents/loz/agent/models.json"))
    k = (mj.get("providers", {}).get("deepseek") or {}).get("apiKey")
    if isinstance(k, str) and k:
        keys["deepseek"] = k
except Exception:
    pass
path = f"{oc}/secrets/llm-keys.json"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(keys, f, indent=2)
print("bootstrapped providers:", sorted(keys))
PY
fi
chmod 600 "$OC/secrets/llm-keys.json"

# 3. Locked-down SSH entry for the GitHub "Sync LLM keys" and watchdog workflows.
if ! id keysync >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/sh keysync
fi
install -d -o keysync -g keysync -m 0700 /home/keysync/.ssh
if [ -n "$PUBKEY" ]; then
  # restrict = no pty, no forwarding. The forced command ignores whatever the client asks
  # to run and hands it to openclaw-keyctl, which allows only `apply` and `status`.
  echo "restrict,command=\"sudo -n /usr/local/sbin/openclaw-keyctl --ssh \\\"\$SSH_ORIGINAL_COMMAND\\\"\" $PUBKEY" \
    > /home/keysync/.ssh/authorized_keys
  chown keysync:keysync /home/keysync/.ssh/authorized_keys
  chmod 600 /home/keysync/.ssh/authorized_keys
fi
echo 'keysync ALL=(root) NOPASSWD: /usr/local/sbin/openclaw-keyctl --ssh *' > /etc/sudoers.d/openclaw-keysync
chmod 440 /etc/sudoers.d/openclaw-keysync
visudo -cf /etc/sudoers.d/openclaw-keysync >/dev/null

# 4. Root cron: preflight before the 15:00 London brief, delivery checks after it.
crontab -l > "$OC/cron/root-crontab.bak-keyctl-$STAMP" 2>/dev/null || true
{ crontab -l 2>/dev/null | grep -v 'openclaw-keyctl' | grep -v '^CRON_TZ=Europe/London$' || true
  echo 'CRON_TZ=Europe/London'
  echo '30 14 * * * /usr/local/sbin/openclaw-keyctl preflight >> /var/log/openclaw-keyctl.log 2>&1'
  echo '45 15 * * * /usr/local/sbin/openclaw-keyctl postcheck >> /var/log/openclaw-keyctl.log 2>&1'
  echo '45 16 * * * /usr/local/sbin/openclaw-keyctl postcheck >> /var/log/openclaw-keyctl.log 2>&1'
} | crontab -

# 5. Wire every agent to the key file (gateway stopped while files are edited).
systemctl --user stop openclaw-gateway
python3 /usr/local/lib/openclaw-keyctl/configure_openclaw.py || { systemctl --user start openclaw-gateway; exit 1; }
systemctl --user start openclaw-gateway
sleep 8
systemctl --user is-active openclaw-gateway
/usr/local/sbin/openclaw-keyctl test
