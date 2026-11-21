"""The digest context names models by their display label (#1018).

#1016 renamed the ``ukmo`` slot UKV and ``meteofrance`` AROME in the app, but
the prompt still printed the slot keys (``Sounding [meteofrance]``, "ukmo sees
RED"), so the briefer could write UKMO / Météo-France against its own naming
rule. Keys stay the lookup currency; only the printed text changes.
"""

from __future__ import annotations

import re
from datetime import datetime

from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
    ForecastSnapshot,
    IcingRisk,
    IcingType,
    IcingZone,
    ModelAdvisoryResult,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    SoundingAnalysis,
    ThermodynamicIndices,
    WaypointAnalysis,
)

TARGET = datetime(2026, 2, 17, 9, 0, 0)
RAW_KEYS = re.compile(r"ukmo|meteofrance|UKMO|METEOFRANCE")


def _sounding() -> SoundingAnalysis:
    zone = IcingZone(base_ft=2200, top_ft=3200, risk=IcingRisk.MODERATE,
                     icing_type=IcingType.MIXED, mean_wet_bulb_c=-4.0)
    deck = EnhancedCloudLayer(base_ft=3000.0, top_ft=8000.0, coverage=CloudCoverage.BKN,
                              mean_cloud_cover_pct=72.0, mean_temperature_c=4.0,
                              source="nwp_3d")
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=4000.0),
        icing_zones=[zone],
        cloud_layers=[deck],
        nwp_cloud_layers=[deck],
    )


def _context(sample_route) -> str:
    snapshot = ForecastSnapshot(
        route=sample_route, target_date="2026-02-17", fetch_date="2026-02-15",
        days_out=2,
        analyses=[WaypointAnalysis(
            waypoint=sample_route.waypoints[0], target_time=TARGET,
            sounding={"ukmo": _sounding(), "meteofrance": _sounding()},
        )],
    )
    R, G = AdvisoryStatus.RED, AdvisoryStatus.GREEN
    manifest = RouteAdvisoriesManifest(
        advisories=[RouteAdvisoryResult(
            advisory_id="ifr_feasibility",
            aggregate_status=R,
            aggregate_detail="Arr EDFE LIFR ceiling 57ft",
            per_model=[
                ModelAdvisoryResult(model="ukmo", status=R, detail="d"),
                ModelAdvisoryResult(model="meteofrance", status=G, detail="d"),
                ModelAdvisoryResult(model="ecmwf", status=G, detail="d"),
            ],
        )],
        catalog=[AdvisoryCatalogEntry(
            id="ifr_feasibility", name="IFR Feasibility",
            short_description="", description="", category="ifr",
        )],
    )
    return build_digest_context(snapshot, TARGET, route_advisories=manifest)


def test_context_prints_display_labels_not_slot_keys(sample_route):
    ctx = _context(sample_route)

    for label in ("UKV", "AROME"):
        assert f"Sounding [{label}]: FzLvl=4000ft" in ctx
        assert f"NWP cloud [{label}]: BKN 3000-8000ft" in ctx
        assert f"Icing zone [{label}]: moderate mixed 2200-3200ft" in ctx
    assert "(minority view — only UKV sees RED; AROME sees GREEN; ECMWF sees GREEN)" in ctx

    assert not RAW_KEYS.search(ctx), RAW_KEYS.findall(ctx)
