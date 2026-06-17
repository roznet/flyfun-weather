"""Shared pack-loading, advisory-summary, and situation-tagging helpers.

These were originally private to ``scripts/extract_digest_eval.py``; they are
factored here so the extract script, the corpus pull script, and the workbench
API all tag fixtures the *same* way (no drift between the eval set and the
labelling corpus). ``extract_digest_eval.py`` re-imports from this module.

Import-light: depends only on ``weatherbrief.models`` + stdlib so it stays
cheap to import from scripts and unit tests.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from weatherbrief.models import ForecastSnapshot

# Map an advisory_id to the situation tag it contributes when its aggregate is
# not green. Several ids fold into one tag (icing_escape + fiki_icing +
# freezing_level -> "icing"). Unmapped ids are ignored for tagging.
ADVISORY_SITUATION: dict[str, str] = {
    "icing_escape": "icing",
    "fiki_icing": "icing",
    "freezing_level": "icing",
    "convective": "convective",
    "turbulence": "turbulence",
    "mountain_wind": "turbulence",
    "cloud_top": "cloud",
    "vmc_cruise": "cloud",
    "airport_wind": "wind",
    "flight_category": "low_category",
    "vfr_feasibility": "vfr_marginal",
    "ifr_feasibility": "ifr_marginal",
}

# The coverage matrix the curated/golden set aims to fill (parent #252 / #254).
# A fixture may carry several tags; these are the cells the coverage report
# scores against.
SITUATION_VOCAB: tuple[str, ...] = (
    "all_green", "single_red", "multi_red",
    "icing", "convective", "icing_plus_convective", "cloud", "turbulence",
    "wind", "low_category", "vfr_marginal", "ifr_marginal",
    "d0", "metar", "sigmet", "previous_digest",
    "us_route", "alpine", "channel_crossing",
)


def load_snapshot_from_pack(pack_dir: Path) -> ForecastSnapshot | None:
    """Load a ForecastSnapshot from a pack directory (briefing + forecasts)."""
    briefing_path = pack_dir / "briefing.json"
    forecasts_path = pack_dir / "forecasts.json"
    if not briefing_path.exists() or not forecasts_path.exists():
        return None

    briefing = json.loads(briefing_path.read_text())
    forecasts = json.loads(forecasts_path.read_text())
    briefing["forecasts"] = forecasts.get("forecasts", [])
    return ForecastSnapshot.model_validate(briefing)


def extract_advisory_summary(adv_path: Path) -> dict:
    """Extract a compact advisory summary (counts + per-advisory aggregate)."""
    if not adv_path.exists():
        return {"has_advisories": False}

    advs = json.loads(adv_path.read_text())
    summary: dict = {"has_advisories": True, "advisories": []}

    for adv in advs.get("advisories", []):
        aid = adv.get("advisory_id", "")
        if aid == "model_agreement":
            continue
        model_statuses: dict[str, int] = defaultdict(int)
        for m in adv.get("per_model", []):
            model_statuses[m.get("status", "unknown")] += 1
        summary["advisories"].append({
            "id": aid,
            "aggregate": adv.get("aggregate_status", "unknown"),
            "models": dict(model_statuses),
        })

    green = sum(1 for a in summary["advisories"] if a["aggregate"] == "green")
    amber = sum(1 for a in summary["advisories"] if a["aggregate"] == "amber")
    red = sum(1 for a in summary["advisories"] if a["aggregate"] == "red")
    summary["counts"] = {"green": green, "amber": amber, "red": red}

    return summary


def classify_situations(
    snapshot: ForecastSnapshot, adv_summary: dict, context: str, days_out: int
) -> list[str]:
    """Tag a pack with the meteorological/structural situations it covers."""
    tags: set[str] = set()

    counts = adv_summary.get("counts", {})
    reds = counts.get("red", 0)
    non_green = [
        a for a in adv_summary.get("advisories", []) if a["aggregate"] != "green"
    ]
    if adv_summary.get("has_advisories") and not non_green:
        tags.add("all_green")
    if reds == 1:
        tags.add("single_red")
    elif reds >= 2:
        tags.add("multi_red")

    for adv in non_green:
        tag = ADVISORY_SITUATION.get(adv["id"])
        if tag:
            tags.add(tag)
    if "icing" in tags and "convective" in tags:
        tags.add("icing_plus_convective")

    # Structural / context-derived tags.
    if days_out == 0:
        tags.add("d0")
    if "=== METAR/TAF OBSERVATIONS" in context:
        tags.add("metar")
    if "=== SIGMETs ALONG ROUTE" in context:
        tags.add("sigmet")
    if "=== PREVIOUS DIGEST" in context:
        tags.add("previous_digest")

    # Route/region tags from *airport* ICAOs only. Route waypoints also include
    # navaids/fixes (3-letter "DVR", 5-letter "KONAN") that would false-match
    # region prefixes (e.g. "KONAN" -> us_route). Real ICAO airport codes are
    # exactly 4 letters, so filter to those.
    icaos = [
        (wp.icao or "").upper()
        for wp in snapshot.route.waypoints
        if len(wp.icao or "") == 4
    ]
    if any(c.startswith(("K", "PA", "PH")) for c in icaos):
        tags.add("us_route")
    # Switzerland (LS) / Austria (LO) prefixes are a coarse Alpine proxy.
    if any(c.startswith(("LS", "LO")) for c in icaos):
        tags.add("alpine")
    if any(c.startswith("EG") for c in icaos) and any(
        c.startswith("LF") for c in icaos
    ):
        tags.add("channel_crossing")

    return sorted(tags)
