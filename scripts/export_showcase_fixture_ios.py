"""Write the iOS showcase fixture: one real briefing for App Store screenshots.

Two steps, so the trimmed fixture can be rebuilt after prod retention has
dropped the flight:

* ``fetch`` reads every endpoint the briefing screens call from a running
  server (a devserver after ``scripts/ops/import_flight.py FLIGHT``) and saves
  the untouched responses, all models, as one ``api_responses.json.gz`` in the
  private eval-set repo (``demo-fixtures/<flight>/``; LFS there).
* ``trim`` turns that dump into the fixture the mock app serves
  (app/fixtures/showcase/showcase.json): ECMWF
  only, no live layer, no surface charts / GRAMET (only what the app draws
  natively appears in screenshots), and a fictional aircraft — this repo is
  public. ``FixtureShowcaseData.swift`` shifts its dates to tomorrow at load.

Usage:
    venv/bin/python scripts/export_showcase_fixture_ios.py fetch FLIGHT_ID [--server URL]
    venv/bin/python scripts/export_showcase_fixture_ios.py trim [RAW_GZ]
    venv/bin/python scripts/export_showcase_fixture_ios.py all FLIGHT_ID
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FLIGHT = "egtf_big_rem_sapre_lsgs-2026-10-12-8bf2"
FIXTURE = REPO / "app/fixtures/showcase/showcase.json"
MODEL = "ecmwf"
# Per-model dicts inside each route point (route-analyses and snapshot).
PER_MODEL_KEYS = ("sounding", "wind_components")
FAKE_AIRCRAFT = {"tail_number": "ZZ-DEMO", "nickname": "Demo"}


def evalset_dir() -> Path:
    """The eval-set repo: EVAL_CORPUS_DIR's parent, else the sibling checkout."""
    corpus = os.environ.get("EVAL_CORPUS_DIR")
    if not corpus:
        env = REPO / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("EVAL_CORPUS_DIR="):
                    corpus = line.split("=", 1)[1].strip().strip('"')
    if corpus:
        return Path(corpus).parent
    return REPO.parents[1] / "flyfun-weather-evalset"


def raw_path(flight_id: str) -> Path:
    return evalset_dir() / "demo-fixtures" / flight_id / "api_responses.json.gz"


# --- fetch ---------------------------------------------------------------------


def _get(server: str, path: str):
    with urllib.request.urlopen(server.rstrip("/") + path, timeout=60) as resp:
        return json.load(resp)


def fetch(flight_id: str, server: str) -> Path:
    base = f"/api/flights/{flight_id}"
    flight = next(f for f in _get(server, "/api/flights") if f["id"] == flight_id)
    pack = _get(server, f"{base}/packs/latest")
    ts = urllib.parse.quote(pack["fetch_timestamp"], safe="")
    p = f"{base}/packs/{ts}"
    out: dict = {
        "flight": flight,
        "packs": _get(server, f"{base}/packs"),
        "latest_pack": pack,
        "advisories": _get(server, f"{p}/advisories"),
        "digest": _get(server, f"{p}/digest/json"),
        "snapshot": _get(server, f"{p}/snapshot"),
        "route_analyses": _get(server, f"{p}/route-analyses"),
        "elevation": _get(server, f"{p}/elevation"),
    }
    out["advisory_details"] = {
        a["advisory_id"]: _get(server, f"{p}/advisories/{a['advisory_id']}/detail")
        for a in out["advisories"]["advisories"]
    }
    out["sounding_profiles"] = {
        model: {
            str(pt["point_index"]): _get(server, f"{p}/sounding-profile/{pt['point_index']}/{model}")
            for pt in out["route_analyses"]["analyses"]
        }
        for model in out["route_analyses"]["models"]
    }
    dest = raw_path(flight_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"{dest}  ({dest.stat().st_size / 1e6:.1f} MB gz)")
    return dest


# --- trim ----------------------------------------------------------------------


def _ecmwf_point(point: dict) -> dict:
    point = dict(point)
    for key in PER_MODEL_KEYS:
        if isinstance(point.get(key), dict):
            point[key] = {MODEL: point[key][MODEL]} if MODEL in point[key] else {}
    alt = point.get("altitude_advisories")
    if isinstance(alt, dict) and isinstance(alt.get("regimes"), dict):
        point["altitude_advisories"] = {**alt, "regimes": {MODEL: alt["regimes"].get(MODEL, [])}}
    # Spread across models means nothing once only one is kept.
    point["model_divergence"] = []
    return point


FETCH_KEYS = ("fetch_timestamp", "fetch_date", "fetch_time")
DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _identifying(raw: dict) -> set[str]:
    """The owner's identifiers in this dump: the public fixture must carry none.

    Read from the data rather than listed here, so this public script names no
    real aircraft or account.
    """
    flight = raw["flight"]
    aircraft = flight.get("aircraft") or {}
    values = {flight.get("user_id"), flight.get("owner_display_name"), flight.get("share_code"),
              aircraft.get("tail_number"), aircraft.get("nickname")}
    return {v for v in values if isinstance(v, str) and len(v) >= 3}


def _as_day_before(node):
    """Present the briefing as D-1: made the day before the flight.

    The app shifts the flight to tomorrow at load, so a D-0 pack would be
    fetched tomorrow too — in the future, with its observations stamped
    "just now". D-1 moves only when it was briefed: forecast valid times stay.
    """
    if isinstance(node, list):
        return [_as_day_before(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "days_out" and isinstance(value, int):
            value = 1
        elif key in FETCH_KEYS and isinstance(value, str) and DATE_PREFIX.match(value):
            day = date.fromisoformat(value[:10]) - timedelta(days=1)
            value = day.isoformat() + value[10:]
        out[key] = _as_day_before(value)
    return out


def trim(raw: dict) -> dict:
    raw = _as_day_before(raw)
    flight = dict(raw["flight"])
    flight["user_id"] = "showcase"
    # `section` is the server's Future/Recent/Past bucket as of the fetch, and
    # it outranks the dates in the app; the debrief was written after flying.
    # The shifted dates make the flight upcoming, so neither applies.
    flight.update(section=None, debrief=None)
    if flight.get("aircraft"):
        flight["aircraft"] = {**flight["aircraft"], **FAKE_AIRCRAFT}
    if flight.get("latest_briefing"):
        flight["latest_briefing"] = {**flight["latest_briefing"], "live_updated_at": None}

    def pack_meta(pack: dict) -> dict:
        # No charts manifest / GRAMET: screenshots show only native renders.
        pack = {k: v for k, v in pack.items() if not k.startswith(("dwd_charts", "metoffice_charts"))}
        pack.update(has_gramet=False, charts=None)
        return pack

    ra = dict(raw["route_analyses"])
    ra["models"] = [MODEL]
    ra["analyses"] = [_ecmwf_point(a) for a in ra["analyses"]]

    snap = dict(raw["snapshot"])
    snap["analyses"] = [_ecmwf_point(a) for a in snap["analyses"]]
    # Radar / satellite / lightning summaries are imagery-derived and were
    # live at briefing time: not what a screenshot should show.
    snap.update(observed_conditions=None, live_changes=None, live_updated_at=None)

    # Skew-T detail: the named waypoints only (every point is ~18 KB).
    waypoints = {str(a["point_index"]) for a in ra["analyses"] if a.get("waypoint_icao")}
    soundings = {k: v for k, v in raw["sounding_profiles"].get(MODEL, {}).items() if k in waypoints}

    out = {
        "flight": flight,
        "pack": pack_meta(raw["latest_pack"]),
        "advisories": raw["advisories"],
        "advisory_details": raw["advisory_details"],
        "digest": raw["digest"],
        "snapshot": snap,
        "route_analyses": ra,
        "elevation": raw["elevation"],
        "sounding_profiles": soundings,
    }
    text = json.dumps(out)
    leaked = sorted(t for t in _identifying(raw) if t in text) + EMAIL.findall(text)
    if leaked:
        raise SystemExit(f"refusing to write: {leaked} still in the fixture")
    return out


def write_fixture(raw_gz: Path) -> None:
    with gzip.open(raw_gz, "rt") as fh:
        raw = json.load(fh)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(trim(raw), separators=(",", ":")))
    print(f"{FIXTURE}  ({FIXTURE.stat().st_size / 1e6:.2f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("flight", nargs="?", default=DEFAULT_FLIGHT)
    f.add_argument("--server", default="http://localhost:8000")
    t = sub.add_parser("trim")
    t.add_argument("raw", nargs="?", type=Path, default=raw_path(DEFAULT_FLIGHT))
    a = sub.add_parser("all")
    a.add_argument("flight", nargs="?", default=DEFAULT_FLIGHT)
    a.add_argument("--server", default="http://localhost:8000")
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.flight, args.server)
    elif args.cmd == "trim":
        write_fixture(args.raw)
    else:
        write_fixture(fetch(args.flight, args.server))
    return 0


if __name__ == "__main__":
    sys.exit(main())
