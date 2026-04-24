"""Ground-truth test: did pilot-cancelled days actually show bad weather?

Parses a freeform `cancel.md` where each line is a cancellation entry:

    DD-MMM-YYYY: DEP ARR [morning] canceled [, instead went DD-MMM-YYYY [DEP ARR]]

For each entry:
  1. Analyze the cancelled date (Hewson pipeline, same as analyze_flight_log).
  2. If a replacement date is given, analyze it too and compute Δscore.
  3. Emit a markdown table — pilot judgement vs system judgement.

Rationale: the main flight-log retrospective is biased — every logged
flight was a "flyable" day by definition, so we only see "some weather"
samples. Cancellations are the pilot's explicit "no-go" signal and the
closest thing we have to bad-weather ground truth.

Known limitations (surface in the report):
  - Times not in `cancel.md`; default 09Z for "morning", else 12Z.
  - Round-trip cancellations: if a trip had dep→arr + return 2-3d later,
    cancellation may have been driven by *either* leg's weather. We
    analyze the stated date only; pilot adjusts manually.
  - ERA5 window 2025-02-01 → 2026-02-28; earlier entries skipped.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_flight_log import (  # noqa: E402
    AIRPORTS_DB_DEFAULT,
    GRIB_DIR_DEFAULT,
    Flight,
    _prime_terrain_mask,
    analyze_one_flight,
)

logger = logging.getLogger("analyze_cancellations")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Example line forms (all lowercased before matching):
#   7-jun-2025: egtf eick canceled, instead went 8-jun-2025 egtf egjb
#   19-jul-2025: egtf lfat canceled
#   24-oct-2025: egtf lsgs morning canceled
#   27-apr-2025: lfmd egtf morning canceled, instead went 28-apr-2025
_ENTRY_RE = re.compile(
    r"^\s*(?P<d1>\d{1,2})-(?P<m1>[a-z]{3})-(?P<y1>\d{4})\s*:\s*"
    r"(?P<dep>[a-z]{4})\s+(?P<arr>[a-z]{4})\s+"
    r"(?P<morning>morning\s+)?"
    r"cancel(?:l)?ed"
    r"(?:\s*,\s*instead\s+went\s+"
    r"(?P<d2>\d{1,2})-(?P<m2>[a-z]{3})-(?P<y2>\d{4})"
    r"(?:\s+(?P<dep2>[a-z]{4})\s+(?P<arr2>[a-z]{4}))?"
    r")?"
    r"\s*$"
)


def _parse_date(d: str, m: str, y: str) -> date:
    return date(int(y), _MONTHS[m], int(d))


def _make_flight(d: date, dep: str, arr: str, morning: bool) -> Flight:
    hour = 9 if morning else 12
    etd = datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)
    return Flight(d, etd, None, dep.upper(), arr.upper())


def parse_cancel_md(path: Path) -> list[dict]:
    """Return list of {cancel: Flight, replacement: Flight | None, morning: bool}."""
    entries: list[dict] = []
    for raw in path.read_text().splitlines():
        line = raw.strip().lower()
        if not line:
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            logger.warning("Unparsed line: %r", raw)
            continue

        cancel_date = _parse_date(m["d1"], m["m1"], m["y1"])
        morning = bool(m["morning"])
        cancel_flight = _make_flight(cancel_date, m["dep"], m["arr"], morning)

        replacement_flight: Flight | None = None
        if m["d2"]:
            replacement_date = _parse_date(m["d2"], m["m2"], m["y2"])
            dep2 = (m["dep2"] or m["dep"]).upper()
            arr2 = (m["arr2"] or m["arr"]).upper()
            replacement_flight = _make_flight(
                replacement_date, dep2, arr2, morning,
            )

        entries.append({
            "cancel": cancel_flight,
            "replacement": replacement_flight,
            "morning": morning,
        })
    return entries


def _fmt_result(r: dict | None) -> str:
    if r is None:
        return "_(no data)_"
    agg = r["agg"]
    t = r["tendency"]
    t_str = f"{t:+.2f}" if not np.isnan(t) else "NaN"
    adv = ", ".join(r["advisories"]) or "(none)"
    return (
        f"score {r['score']:.2f} · "
        f"Δθe {agg['theta_e_delta']:.1f} K · "
        f"|∇θe|max {agg['grad_max']:.2f} · "
        f"adv {agg['adv_max_signed']:+.2f} · "
        f"tend {t_str} · "
        f"TFP⇄ {agg['tfp_zero_crossings']}  \n  "
        f"**Advisories**: {adv}  \n  "
        f"**Physical read**: {r['physical_read']}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cancel-md", required=True)
    p.add_argument("--output", default="flight_cancel_retrospective.md")
    p.add_argument("--era5-dir", default=str(GRIB_DIR_DEFAULT))
    p.add_argument("--airports-db", default=str(AIRPORTS_DB_DEFAULT))
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    era5_dir = Path(args.era5_dir)
    airports_db = Path(args.airports_db)
    entries = parse_cancel_md(Path(args.cancel_md))
    logger.info("Parsed %d cancellation entries", len(entries))

    # Prime the terrain mask from any entry's GRIB
    flights_for_priming = [e["cancel"] for e in entries] + [
        e["replacement"] for e in entries if e["replacement"]
    ]
    terrain_mask = _prime_terrain_mask(flights_for_priming, era5_dir)

    results: list[dict] = []
    for e in entries:
        cancel_r = analyze_one_flight(e["cancel"], era5_dir, airports_db, terrain_mask)
        replacement_r = None
        if e["replacement"]:
            replacement_r = analyze_one_flight(e["replacement"], era5_dir, airports_db, terrain_mask)
        results.append({
            "cancel_flight": e["cancel"],
            "cancel_result": cancel_r,
            "replacement_flight": e["replacement"],
            "replacement_result": replacement_r,
            "morning": e["morning"],
        })

    _write_report(Path(args.output), results)
    print(f"Wrote {args.output}")


def _write_report(out: Path, results: list[dict]) -> None:
    n = len(results)
    n_with_replacement = sum(1 for r in results if r["replacement_flight"])

    with out.open("w") as fh:
        fh.write("# Cancelled-Flight Retrospective\n\n")
        fh.write(
            f"{n} cancellation entries parsed; {n_with_replacement} have a replacement day.\n\n"
        )
        fh.write(
            "Default times: 09Z for 'morning canceled', 12Z otherwise. "
            "Thresholds as in the main retrospective.\n\n"
        )
        fh.write("---\n\n## Pairwise comparison (cancel vs replacement)\n\n")

        pairs = [r for r in results if r["replacement_flight"]]
        if not pairs:
            fh.write("_(no pairs with replacement day in this data)_\n\n")
        else:
            fh.write(
                "For each pair, if `score(cancel) > score(replacement)` the system "
                "agrees with your decision. Sign of Δ = cancel − replacement.\n\n"
            )
            n_agree = 0
            for r in pairs:
                cf: Flight = r["cancel_flight"]
                rf: Flight = r["replacement_flight"]
                cr = r["cancel_result"]
                rr = r["replacement_result"]
                if cr and rr:
                    delta = cr["score"] - rr["score"]
                    agree = "✅ agrees" if delta > 0 else "❌ disagrees"
                    if delta > 0:
                        n_agree += 1
                else:
                    delta = float("nan")
                    agree = "_(missing data)_"
                fh.write(
                    f"### {cf.date} {cf.dep_icao}→{cf.arr_icao} "
                    f"(cancelled, {'morning' if r['morning'] else 'midday'}) "
                    f"vs {rf.date} (flown)\n\n"
                )
                fh.write(f"- **Δscore (cancel − replacement): {delta:+.2f}** {agree}\n")
                fh.write(f"- CANCEL  ({cf.date}): {_fmt_result(cr)}\n")
                fh.write(f"- FLEW    ({rf.date}): {_fmt_result(rr)}\n\n")
            fh.write(
                f"**Pairwise agreement: {n_agree}/{len(pairs)}** "
                f"(system's score ranks cancel-day higher than replacement-day).\n\n"
            )

        fh.write("---\n\n## All cancellations (absolute view)\n\n")
        fh.write(
            "Sorted by cancel-day score descending. Loud scores here are the positive "
            "signal we want — 'pilot said no, and the system says it was indeed bad'.\n\n"
        )
        results_sorted = sorted(
            results,
            key=lambda r: -(r["cancel_result"]["score"] if r["cancel_result"] else -1),
        )
        for r in results_sorted:
            cf: Flight = r["cancel_flight"]
            cr = r["cancel_result"]
            fh.write(
                f"### {cf.date} {cf.dep_icao}→{cf.arr_icao} "
                f"({'morning' if r['morning'] else 'midday'} canceled)\n\n"
            )
            fh.write(f"- CANCEL: {_fmt_result(cr)}\n")
            if r["replacement_flight"]:
                rf: Flight = r["replacement_flight"]
                rr = r["replacement_result"]
                fh.write(f"- Replacement flown on {rf.date}: {_fmt_result(rr)}\n")
            fh.write("\n")


if __name__ == "__main__":
    main()
