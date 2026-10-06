#!/usr/bin/env python3
"""Mint the scoped PostgREST token for Lozatron's learning loop.

WHY THIS SCRIPT EXISTS, AND WHY YOU RUN IT RATHER THAN CLAUDE

The learning loop runs in a public repository's GitHub Actions and needs a
standing credential to read Lauren's button feedback. The obvious choice,
`service_role`, bypasses RLS on every table in the Mindmaker OS project --
contacts, app_secrets, contact_intelligence. That key has no business being
the standing credential for one read-only function.

So the loop uses a token carrying the `lozatron_reader` role instead. That
role can execute `public.loz_recent_signals` and read no table at all;
`lozatron.signals` and `public.contact_intelligence` were both checked and
denied.

Minting it needs the project's JWT secret, which is strictly more dangerous
than the service key: it signs *any* role, including service_role, with any
expiry you like. It must never be pasted into a chat, a transcript, a commit
or a CI log. Hence a local script -- the secret stays on your machine and only
the scoped token leaves it.

USAGE

    # Supabase dashboard > Project Settings > API > JWT Settings > JWT Secret
    read -rs JWT_SECRET            # typed, not echoed, not in shell history
    export JWT_SECRET
    python3 scripts/mint_reader_token.py

Then add the printed token to the repository's Actions secrets as
LOZ_SIGNAL_READ_KEY, and unset JWT_SECRET.

Standard library only, to match the rest of this repository.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

PROJECT_REF = "gojpffsrxybbpbdzzrvs"
ROLE = "lozatron_reader"

# Two years. Long enough not to be a recurring chore, short enough that a
# leaked token does not outlive the product. The service key it replaces
# expires in 2036, which is effectively never.
LIFETIME_SECONDS = 2 * 365 * 24 * 3600


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> int:
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        print(
            "JWT_SECRET is not set.\n\n"
            "  Supabase dashboard > Project Settings > API > JWT Settings\n"
            "  read -rs JWT_SECRET && export JWT_SECRET\n",
            file=sys.stderr,
        )
        return 1

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": "supabase",
        "ref": PROJECT_REF,
        "role": ROLE,
        "iat": now,
        "exp": now + LIFETIME_SECONDS,
    }
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{b64url(signature)}"

    print(token)
    print(
        f"\nrole={ROLE}  project={PROJECT_REF}  "
        f"expires={time.strftime('%Y-%m-%d', time.gmtime(claims['exp']))}\n"
        "Add as repository secret LOZ_SIGNAL_READ_KEY, then `unset JWT_SECRET`.\n"
        "Verify with:\n"
        f"  curl -sS -X POST \"https://{PROJECT_REF}.supabase.co/rest/v1/rpc/"
        "loz_recent_signals\" \\\n"
        "    -H \"apikey: $TOKEN\" -H \"Authorization: Bearer $TOKEN\" \\\n"
        "    -H 'Content-Type: application/json' -d '{\"p_days\":30}'",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
