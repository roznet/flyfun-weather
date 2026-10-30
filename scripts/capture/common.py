"""Shared plumbing for the per-platform flight captures (`ios.py`, `android.py`, `web.py`).

Every platform captures the same named screens of one flight, against the
running `/devserver --https` (`https://localhost.ro-z.me:8443`), signed in as
the dev user with a short-lived token minted here. The flight must be in the
dev DB: `scripts/ops/import_flight.py <id>` brings a prod flight there.

Output, the same shape for every platform:

    build/captures/<flight>/<platform>/<device>/<light|dark>/NN-<screen>.png
    build/captures/<flight>/<platform>/manifest.json

The captures show the owner's real data (the flight, the dev user's flight
list): they stay under the gitignored `build/`, never in a commit.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://localhost.ro-z.me:8443"
OUT_ROOT = ROOT / "build/captures"

# The canonical screens, numbered the same on every platform so a doc can name
# `05-cross-section` and find it in each set. A platform that has no such
# screen, or a flight that doesn't (observed is flight-day only), leaves the
# number out; the manifest lists what was skipped.
SCREENS = ["flights", "briefing", "advisories", "discussion", "cross-section", "skewt", "map", "observed"]
# Screens a flight may legitimately not have: missing them is not a failure.
OPTIONAL = {"observed"}
# Covers a cold iOS build between minting and the sign-in deep link; the
# token is signed with the production secret, so no longer than that.
TOKEN_MINUTES = 20


def file_name(screen: str) -> str:
    return f"{SCREENS.index(screen) + 1:02d}-{screen}"


def parse_screens(value: str | None) -> list[str]:
    if not value:
        return list(SCREENS)
    wanted = [s.strip() for s in value.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SCREENS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown screen(s) {unknown}; choose from {SCREENS}")
    return [s for s in SCREENS if s in wanted]


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--flight", required=True, type=flight_id, help="flight id (or briefing.html?flight=ID URL) in the dev DB")
    ap.add_argument("--screens", type=parse_screens, default=list(SCREENS),
                    help=f"comma-separated subset of {','.join(SCREENS)} (default all)")
    ap.add_argument("--appearance", action="append", choices=["light", "dark"], help="repeatable; default light")
    ap.add_argument("--out", type=Path, default=None, help=f"default {OUT_ROOT.relative_to(ROOT)}/<flight>/<platform>")


def flight_id(value: str) -> str:
    """A bare id, or the id out of a `briefing.html?flight=ID` URL."""
    if "flight=" in value:
        return value.split("flight=", 1)[1].split("&", 1)[0]
    return value.strip()


def out_dir(args: argparse.Namespace, platform: str) -> Path:
    return args.out or OUT_ROOT / args.flight / platform


def load_env() -> None:
    """The checkout's `.env` (JWT_SECRET, DATA_DIR), without overriding the environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a dev dependency
        return
    load_dotenv(ROOT / ".env")


def mint_token(minutes: int = TOKEN_MINUTES) -> str:
    """A short-lived session JWT for the dev user.

    `scope: review` is what lets the apps accept a bare token deep link
    (`flyfunweather://auth?token=`). The dev `.env` JWT_SECRET is the
    production secret, so the token is kept short and never printed.
    """
    import jwt
    from flyfun_common.db import DEV_USER_ID

    load_env()
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        sys.exit("error: JWT_SECRET is not set (.env)")
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": DEV_USER_ID, "email": "dev@localhost", "name": "Dev User",
                       "scope": "review", "iat": now, "exp": now + timedelta(minutes=minutes)},
                      secret, algorithm="HS256")


def _get(path: str, token: str | None = None, timeout: float = 10) -> tuple[int, bytes]:
    req = urllib.request.Request(BASE_URL + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def preflight(flight: str) -> str:
    """Check the devserver and the flight, and return a fresh token. Exits with a hint on failure."""
    try:
        status, _ = _get("/health", timeout=5)
    except (OSError, urllib.error.URLError) as e:
        sys.exit(f"error: no devserver at {BASE_URL} ({e}). Start it: python3 scripts/ops/devserver.py --https")
    if status != 200:
        sys.exit(f"error: {BASE_URL}/health answered {status}")
    token = mint_token()
    status, body = _get(f"/api/flights/{flight}", token)
    if status == 404:
        sys.exit(f"error: flight {flight} is not in the dev DB for the dev user. "
                 f"Import it: venv/bin/python scripts/ops/import_flight.py {flight}")
    if status != 200:
        sys.exit(f"error: GET /api/flights/{flight} answered {status}: {body[:200]!r}")
    status, _ = _get(f"/api/flights/{flight}/packs", token)
    if status != 200:
        sys.exit(f"error: flight {flight} has no briefing pack on the devserver ({status})")
    return token


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()


def write_manifest(out: Path, platform: str, flight: str, sets: dict[str, dict[str, list[Path]]],
                   wanted: list[str]) -> list[str]:
    """Record what each device/appearance captured; return the required screens that are missing.

    Entries from earlier runs for other devices or appearances are kept: a
    second run at another size adds to the set rather than hiding the first.
    """
    out.mkdir(parents=True, exist_ok=True)
    path = out / "manifest.json"
    try:
        entries = json.loads(path.read_text()).get("captures", {})
    except (OSError, ValueError):
        entries = {}
    stamp = {"commit": git_head(), "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    missing_required = []
    for device, by_appearance in sets.items():
        for appearance, shots in by_appearance.items():
            got = {p.stem.split("-", 1)[1] for p in shots}
            skipped = [s for s in wanted if s not in got]
            missing_required += [f"{device}/{appearance}/{s}" for s in skipped if s not in OPTIONAL]
            entries.setdefault(device, {})[appearance] = {
                # The whole set on disk, not just this run's: a partial re-run keeps the rest.
                "files": sorted(str(p.relative_to(out))
                                for p in (out / device / appearance).glob("[0-9][0-9]-*.png")),
                "skipped": skipped,
                **stamp,
            }
    path.write_text(json.dumps({"flight": flight, "platform": platform, "server": BASE_URL,
                                "captures": entries}, indent=2) + "\n")
    return missing_required


def clear_pngs(dest: Path, screens: list[str]) -> None:
    """Drop an earlier run's captures of `screens`, so one this run skipped can't linger.

    Only those: a `--screens skewt` re-run keeps the rest of the set.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for screen in screens:
        (dest / f"{file_name(screen)}.png").unlink(missing_ok=True)


def report(out: Path, sets: dict[str, dict[str, list[Path]]], missing: list[str]) -> int:
    for device, by_appearance in sets.items():
        for appearance, shots in by_appearance.items():
            names = ", ".join(p.stem for p in sorted(shots)) or "none"
            print(f"{device} / {appearance}: {len(shots)} → {names}")
    print(f"manifest {out / 'manifest.json'}")
    if missing:
        print(f"MISSING {missing}", file=sys.stderr)
        return 1
    return 0
