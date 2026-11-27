#!/usr/bin/env python3
"""Runs INSIDE the weatherbrief container (copied in by fetch.py): print the routine
METARs of a window as JSON lines on stdout.

    python metar_dump.py "2026-11-21 18:00" "2026-11-23 00:00"

One indexed range scan on ``verification_observations.observation_time``
(``ix_verif_obs_time``), only the columns a front-passage detector needs, routine
reports only (SPECIs are a bad-weather-biased sample, same rule as the scorers).
About 400 airports x 2 per hour, so a day is ~20k small rows.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def main() -> None:
    since, until = (datetime.fromisoformat(s).replace(tzinfo=timezone.utc) for s in sys.argv[1:3])
    from flyfun_common.db import SessionLocal, get_engine
    from sqlalchemy import select

    from weatherbrief.db.models import VerificationObservationRow as O
    from weatherbrief.tasks.verification import routine_observation_filter

    get_engine()
    db = SessionLocal()
    try:
        q = (
            select(O.icao, O.observation_time, O.wind_dir, O.wind_speed_kt,
                   O.temperature_c, O.dewpoint_c, O.qnh)
            .where(O.observation_time >= since, O.observation_time < until)
            .where(routine_observation_filter())
        )
        n = 0
        for icao, t, wd, ws, tc, td, qnh in db.execute(q):
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            print(json.dumps({"icao": icao, "t": t.strftime("%Y-%m-%dT%H:%MZ"), "wd": wd, "ws": ws,
                              "tc": tc, "td": td, "qnh": qnh}))
            n += 1
        print(f"{n} rows", file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    main()
