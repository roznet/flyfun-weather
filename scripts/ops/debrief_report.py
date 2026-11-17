#!/usr/bin/env python3
"""Print the debrief report prototype for local flights (phase 0, not for merge).

    venv/bin/python scripts/ops/debrief_report.py FLIGHT_ID...          # readable text
    venv/bin/python scripts/ops/debrief_report.py FLIGHT_ID --json      # the API payload

Reads the local dev DB + DATA_DIR, so import the flight first with
``scripts/ops/import_flight.py``. The same report is served at
``GET /api/flights/{id}/debrief/report`` and shown on ``/debrief-report.html?flight=ID``.
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
    lines.append(f"  interesting: {it.get('interesting')}  "
                 + ", ".join(x["kind"] for x in it.get("reasons") or []))
    if r.get("summary"):
        lines.append(f"\n  {r['summary']['text']}")
    strip = r.get("strip") or []
    if strip:
        f = "".join({"red": "R", "amber": "a", "green": "."}.get(s["forecast"] or "", " ") for s in strip)
        o = "".join({"heavy": "H", "rain": "r", "clear": ".", "no_data": "?", "not_recorded": " "}[s["observed"]]
                    for s in strip)
        lines += ["", f"  forecast |{f}|", f"  observed |{o}|"]
    for a in r.get("airports") or []:
        lines.append(f"  {a['role']:<9} {a['icao']}  {a.get('metar_category') or '-':<5} vs TAF "
                     f"{a.get('taf_category') or '-'}"
                     + (f"/{a.get('taf_temporary_type')} {a.get('taf_temporary_category')}" if a.get("taf_temporary_category") else "")
                     + f"  -> {a.get('match')}   {a.get('metar_raw') or ''}")
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
