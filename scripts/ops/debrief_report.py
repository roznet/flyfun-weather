#!/usr/bin/env python3
"""Print the debrief report (#1023) for local flights.

    venv/bin/python scripts/ops/debrief_report.py FLIGHT_ID...          # readable text
    venv/bin/python scripts/ops/debrief_report.py FLIGHT_ID --json      # the API payload

Reads the local dev DB + DATA_DIR, so import the flight first with
``scripts/ops/import_flight.py``. The same report is served at
``GET /api/flights/{id}/debrief/report``. Use it to replay reviewed flights
after a rules change and compare headlines and questions.
"""

from __future__ import annotations

import argparse
import json
import sys


def _text(r: dict) -> str:
    lines = [f"{' -> '.join(r.get('route') or [])}  [{r.get('flight_id')}]",
             f"  status {r.get('status')}  time base {r.get('time_base')}  "
             f"dep {r.get('departed_at')}  arr {r.get('arrival_at')}  pack {r.get('forecast_pack')}"]
    fa = r.get("flown_altitude") or {}
    lines.append(f"  flown {fa.get('ft')} ft ({fa.get('source')}, {fa.get('fixes')} fixes)")
    if r.get("message"):
        lines.append(f"  {r['message']}")
    it = r.get("interest") or {}
    lines.append(f"  push: {it.get('interesting')}  "
                 + ", ".join(x["kind"] + (f" {x['hazards']}" if x.get("hazards") else "") for x in it.get("reasons") or []))
    if r.get("summary"):
        lines += ["", f"  >> {r['summary']['headline']}", ""]
        lines += [f"     {l['text']}" for l in r["summary"]["lines"]]
    strip = r.get("strip") or []
    if strip:
        o = "".join({"heavy": "H", "rain": "r", "clear": ".", "no_data": "?", "not_recorded": " "}[s["observed"]]
                    for s in strip)
        lines += [f"  radar |{o}|"]
    for q in r.get("questions") or []:
        lines.append(f"\n  Q [{q['id']}] {q['text']}")
        lines.append("     " + " / ".join(o["label"] for o in q["options"])
                     + (f"   answered: {q['answer']['answer']}" if q.get("answer") else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flight_ids", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from weatherbrief.db import SessionLocal, init_shared_db
    from weatherbrief.debriefs.report import report_for_flight

    init_shared_db()
    db = SessionLocal()
    try:
        for fid in args.flight_ids:
            r = report_for_flight(db, fid)
            print(json.dumps(r, indent=1, default=str) if args.json else _text(r))
            print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
