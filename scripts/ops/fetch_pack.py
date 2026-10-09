#!/usr/bin/env python3
"""Get a briefing pack onto local disk for investigation; print its directory.

    fetch_pack.py URL_OR_FLIGHT_ID [--pack TS] [--prod] [--all]

- Accepts a briefing URL (`.../briefing.html?flight=ID&pack=TS`, also `&t=TS`)
  or a bare flight id; `--pack` overrides the URL's pack timestamp.
- Looks in this checkout's DATA_DIR/packs/*/<flight>/ first (no copy), unless
  `--prod` or the flight/pack isn't there.
- From prod it rsyncs ONE pack (the named one, else the newest) into
  DATA_DIR/packs/debug/<flight>/<pack>/; `--all` takes every pack of the flight.
  Resolves the droplet via scripts/ops/hosts.py (HOST_DATA_DIR, not the container's
  DATA_DIR).

Last stdout line is `PACK_DIR=<path>`. Status lines go to stderr. Exit 0 ok, 1 not
found / copy failed, 2 a host could not be reached. Never modifies a pack.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def say(state: str, what: str, evidence: str = "") -> None:
    print(f"{state:<8} {what}" + (f"  ({evidence})" if evidence else ""), file=sys.stderr)


def parse_target(arg: str, pack: str | None) -> tuple[str, str | None]:
    """(flight id, pack timestamp or None) from a URL or a bare id."""
    if "?" in arg or arg.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        flight = (q.get("flight") or q.get("id") or [""])[0]
        pack = pack or (q.get("pack") or q.get("t") or [None])[0]
    else:
        flight = arg
    if not flight or "/" in flight or flight.startswith("."):
        raise SystemExit(f"no usable flight id in {arg!r}")
    return flight, pack


def pack_key(ts: str) -> str:
    """Normalise an API/URL timestamp to the pack directory spelling:
    2026-10-09T13:26:39.716683+00:00 -> 2026-10-09T13-26-39.716683p00-00."""
    return ts.replace(":", "-").replace("+", "p")


def choose(names: list[str], pack: str | None) -> str | None:
    names = sorted(n for n in names if n and not n.startswith("."))
    if not pack:
        return names[-1] if names else None
    key = pack_key(pack)
    exact = [n for n in names if n == key or n == pack]
    return exact[0] if exact else next((n for n in names if n.startswith(key)), None)


def local_pack(data_dir: Path, flight: str, pack: str | None) -> Path | None:
    for flight_dir in sorted(data_dir.glob(f"packs/*/{flight}")):
        name = choose([p.name for p in flight_dir.iterdir() if p.is_dir()], pack)
        if name:
            return flight_dir / name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="briefing URL or flight id")
    ap.add_argument("--pack", help="pack timestamp (directory name or ISO)")
    ap.add_argument("--prod", action="store_true", help="skip the local lookup")
    ap.add_argument("--all", action="store_true", help="every pack of the flight")
    a = ap.parse_args(argv)
    flight, pack = parse_target(a.target, a.pack)

    data = hosts.check_local().values.get("LOCAL_DATA_DIR")
    if not data:
        say("problem", "local DATA_DIR", "did not resolve -- run `hosts.py local`")
        return 1
    data_dir = Path(data)

    if not a.prod:
        found = local_pack(data_dir, flight, pack)
        if found and not a.all:
            say("ok", "local pack", f"already on disk, nothing copied")
            print(f"PACK_DIR={found}")
            return 0

    try:
        v = hosts.server_values("SERVER_SSH", "HOST_DATA_DIR")
    except SystemExit as exc:
        say("unknown", "prod", str(exc).splitlines()[0])
        return 2
    ssh, packs = v["SERVER_SSH"], v["HOST_DATA_DIR"] + "/packs"
    r = subprocess.run([*SSH, ssh, f"cd {shlex.quote(packs)} && ls -d */{shlex.quote(flight)}/*/ "
                        "2>/dev/null"], capture_output=True, text=True, timeout=60)
    if r.returncode == 255:
        say("unknown", "prod", r.stderr.strip()[-200:])
        return 2
    dirs = [d.rstrip("/") for d in r.stdout.split()]
    if not dirs:
        say("problem", "prod", f"no packs for flight {flight} under {packs}")
        return 1
    user = dirs[0].split("/")[0]
    names = [d.split("/")[-1] for d in dirs]
    if a.all:
        src, dest = f"{packs}/{user}/{flight}/", data_dir / "packs" / "debug" / flight
        picked = f"all {len(names)} packs"
    else:
        name = choose(names, pack)
        if not name:
            say("problem", "prod", f"pack {pack} not among {len(names)} packs "
                f"(newest {sorted(names)[-1]})")
            return 1
        src, dest = f"{packs}/{user}/{flight}/{name}/", data_dir / "packs" / "debug" / flight / name
        picked = name + ("" if pack else " (newest)")
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rsync", "-az", "--stats", f"{ssh}:{src}", f"{dest}/"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        say("problem", "rsync", r.stderr.strip()[-300:])
        return 1
    size = next((ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
                 if ln.startswith("Total file size")), "?")
    say("ok", f"prod pack {picked}", f"{size} -> {dest}")
    print(f"PACK_DIR={dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
