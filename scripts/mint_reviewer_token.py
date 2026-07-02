#!/usr/bin/env python3
"""Mint an App Store reviewer sign-in deep link.

The iOS app signs in via a `flyfunweather://auth?token=<jwt>` deep link. The
JWT is self-contained (auth verifies the signature only — it does NOT hit the
DB), so we can mint a long-lived reviewer token locally as long as we sign it
with the SAME `JWT_SECRET` that production validates against. The dev `.env`
JWT_SECRET is the production secret, so this works from a dev checkout.

Why this exists: the standard `flyfun_common.auth.create_token` helper only
issues 7-day tokens. Apple review cycles run longer, so the reviewer token is
minted here with a configurable (default 60-day) expiry.

Nothing sensitive is hardcoded. Everything is read from the environment (`.env`
in dev, gitignored):
  - JWT_SECRET        the real secret; the ONLY sensitive value
  - REVIEWER_USER_ID  the reviewer test account's user id (JWT `sub`)
  - REVIEWER_EMAIL    the reviewer test account's email
  - REVIEWER_NAME     the reviewer test account's display name

The reviewer account is an "Sign in with Apple" test user (private-relay
email). It must already exist in the production DB — this script does not
create it, it only issues a session token for it.

Usage:
    python3 scripts/mint_reviewer_token.py            # 60-day token
    python3 scripts/mint_reviewer_token.py --days 90
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a dev dependency
    load_dotenv = None

JWT_ALGORITHM = "HS256"


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"error: {name} is not set. Add it to .env (which is gitignored). "
            "See the module docstring for the required variables."
        )
    return val


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="token lifetime in days (default: 60)",
    )
    args = parser.parse_args()

    # Load dev .env so the script works from a checkout without exporting vars.
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    secret = _require("JWT_SECRET")
    user_id = _require("REVIEWER_USER_ID")
    email = _require("REVIEWER_EMAIL")
    name = _require("REVIEWER_NAME")

    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=args.days)
    token = jwt.encode(
        # scope:"review" is what lets the hardened app accept this as a
        # bare-token deep link (the App Store reviewer carve-out). It is a
        # routing hint only — the server verifies the signature on every call,
        # so the claim confers no authority on its own. See
        # designs/oauth-deeplink-hardening.md.
        {
            "sub": user_id,
            "email": email,
            "name": name,
            "scope": "review",
            "iat": now,
            "exp": exp,
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )

    # Self-verify so we never hand out a token that won't decode.
    jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])

    print(f"Reviewer:  {name} <{email}>")
    print(f"Issued:    {now.isoformat()}")
    print(f"Expires:   {exp.isoformat()}  ({args.days} days)")
    print()
    print(f"flyfunweather://auth?token={token}")


if __name__ == "__main__":
    main()
