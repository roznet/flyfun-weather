"""Evaluating the clutter evidence over archived catalogues (#696).

The issue's own acceptance condition is not "the reference case disappears" —
it is **that and no genuine core lost**, measured over a replay set.  This is
the measurement, run over catalogues the loop (or ``replay``) already wrote,
so it costs no radar decoding and can sweep months of analysis.

## The labels are cheap, and they are not ground truth

Hand-labelling radar echoes does not scale, so both sides are proxies, taken
from the issue thread's suggestion:

* **weather** — the cell has observed lightning, or a satellite cloud top.
  Lightning that is only *pending* (#666) is not an observation, so those
  cells are ``unknown``, never "no lightning".
* **clutter** — nothing here can assert it, so there is no positive clutter
  label at all.  Precision is reported as *what the flags look like*
  (how strong, how often the same pixels, how often alone in clear air) and
  judged by eye, not scored against a truth we do not have.

So the number that carries weight is **recall of the weather label: how many
lightning-bearing or cloud-topped cores the rule would have removed.**  That
is the quantity the issue puts a bound on, and it should be zero, because
those are vetoes — a non-zero count means a veto leaked and is a bug, not a
calibration result.

The second number is the **flag rate**: what share of cores are held back.  It
has no target, but a jump between regimes (a convective afternoon against a
quiet winter night, France against Scandinavia) is the signal that a threshold
is doing something other than what we think.

Nothing here writes to the archive.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .catalogue import catalogue_path, read_catalogue
from .clutter import CONFIRMED, SUSPECT
from .policy import CellPolicy


def label(cell: dict) -> str:
    """``weather`` / ``unknown`` for one catalogue cell — never ``clutter``.

    Only positive corroboration labels a cell.  Absence of lightning is not
    evidence of clutter (coverage, latency, and plenty of real rain never
    sparks), which is exactly why there is no negative label.
    """
    if cell.get("flashes"):
        return "weather"
    if cell.get("top_fl") is not None:
        return "weather"
    return "unknown"


def frame_rows(catalogue: dict) -> list[dict]:
    """One row per assessed cell of one catalogue."""
    rows: list[dict] = []
    for cell in catalogue.get("cells") or []:
        block = cell.get("clutter")
        if not block:
            continue  # a tier that is not assessed, or a pre-#696 catalogue
        rows.append({
            "valid_time": catalogue.get("valid_time"),
            "id": cell.get("id"),
            "tier": cell.get("tier"),
            "lat": cell.get("lat"),
            "lon": cell.get("lon"),
            "area_km2": cell.get("area_km2"),
            "peak_dbz": cell.get("peak_dbz"),
            "robust_peak_dbz": (block.get("features") or {}).get("robust_peak_dbz"),
            "flashes": cell.get("flashes"),
            "top_fl": cell.get("top_fl"),
            "age_min": (cell.get("lineage") or {}).get("age_min"),
            "motion_status": (cell.get("motion") or {}).get("status"),
            "level": block.get("level"),
            "score": block.get("score"),
            "reasons": [r.get("feature") for r in block.get("reasons") or []],
            "unknown": block.get("unknown") or [],
            "features": block.get("features") or {},
            "label": label(cell),
        })
    return rows


def rows_over(root: Path, start: datetime, end: datetime, policy: CellPolicy,
              *, step_minutes: int = 5) -> list[dict]:
    """Rows for every catalogue between ``start`` and ``end`` under ``root``.

    Catalogues written under a different ``policy_version`` are skipped and
    counted: mixing them would average two different rules together, which is
    the mistake this whole module exists to avoid.
    """
    rows: list[dict] = []
    t = start
    while t <= end:
        catalogue = read_catalogue(catalogue_path(root, t))
        if catalogue is not None and catalogue.get("policy_version") == policy.policy_version:
            rows.extend(frame_rows(catalogue))
        t += timedelta(minutes=step_minutes)
    return rows


def summarise(rows: list[dict]) -> dict:
    """The report: flag rate per tier, label recall, and why cells were flagged."""
    by_tier: dict[str, Counter] = {}
    reasons: Counter = Counter()
    unknown: Counter = Counter()
    lost: list[dict] = []
    peak_delta: list[float] = []
    for row in rows:
        counts = by_tier.setdefault(row["tier"], Counter())
        counts["cells"] += 1
        counts[row["level"]] += 1
        counts[f"label_{row['label']}"] += 1
        if row["level"] in (SUSPECT, CONFIRMED):
            counts["flagged"] += 1
            reasons.update(row["reasons"])
            if row["label"] == "weather":
                counts["weather_lost"] += 1
                lost.append({k: row[k] for k in ("valid_time", "id", "tier", "peak_dbz",
                                                 "flashes", "top_fl", "level", "score", "reasons")})
        unknown.update(row["unknown"])
        robust = row.get("robust_peak_dbz")
        if robust is not None and row.get("peak_dbz") is not None:
            peak_delta.append(float(row["peak_dbz"]) - float(robust))

    def pct(n: int, d: int) -> float | None:
        return round(100.0 * n / d, 2) if d else None

    tiers = {}
    for tier, counts in sorted(by_tier.items()):
        total = counts["cells"]
        tiers[tier] = {
            "cells": total,
            "suspect": counts[SUSPECT],
            "confirmed": counts[CONFIRMED],
            "flagged": counts["flagged"],
            "flagged_pct": pct(counts["flagged"], total),
            "label_weather": counts["label_weather"],
            # The bound the issue sets, and it must be zero: lightning and a
            # cloud top are vetoes, so a flagged cell carrying either is a leak.
            "weather_lost": counts["weather_lost"],
            "weather_lost_pct": pct(counts["weather_lost"], counts["label_weather"]),
        }
    delta = sorted(peak_delta)
    return {
        "frames": len({row["valid_time"] for row in rows}),
        "tiers": tiers,
        "reasons": dict(reasons.most_common()),
        "unknown_features": dict(unknown.most_common()),
        "peak_minus_robust_db": {
            "p50": round(delta[len(delta) // 2], 2) if delta else None,
            "p90": round(delta[int(0.9 * (len(delta) - 1))], 2) if delta else None,
            "max": round(delta[-1], 2) if delta else None,
        },
        "weather_lost_examples": lost[:20],
    }


def write_jsonl(rows: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)
