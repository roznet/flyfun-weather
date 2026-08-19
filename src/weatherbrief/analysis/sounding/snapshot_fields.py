"""Sounding-derived snapshot fields — shared by inline and pooled analysis.

The standalone verification cycle stores a handful of sounding-derived
scalars per (airport, model, hour) snapshot. Historically they were computed
inline in the fetch path (`_enrich_with_sounding`); #448 measured that at
~55–60 single-core minutes per forecast cycle (~56K `analyze_sounding_lite`
calls), so the cycle now ships batches of profiles to the GRIB decode
process pool (PR B). This module holds the one implementation both paths
share:

- :func:`compute_snapshot_sounding_fields` — profile in, scalar fields out.
- :func:`build_sounding_payload` / :func:`analyze_sounding_batch_items` —
  the plain-dict serialisation used across the process boundary. Payloads
  carry only primitives (the worker rebuilds the Pydantic models), keeping
  IPC cheap and spawn-safe.

Fail-silent contract: a profile that cannot be analysed yields ``{}`` —
surface data is always preserved, matching the historical behaviour.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from weatherbrief.analysis.sounding.edr import EdrAccumulator
    from weatherbrief.models.analysis import HourlyForecast

logger = logging.getLogger(__name__)


def compute_snapshot_sounding_fields(
    hourly: "HourlyForecast",
    model_key: str,
    edr_acc: "EdrAccumulator | None" = None,
) -> dict[str, Any]:
    """Run lite sounding analysis on ``hourly.pressure_levels`` → scalar fields.

    Returns the snapshot-column subset (``sounding_ceiling_ft``,
    ``freezing_level_ft``, CAPE/CIN, lifted index, cloud base, convective
    risk) or ``{}`` when there are no pressure levels or analysis fails.
    Never raises.
    """
    if not getattr(hourly, "pressure_levels", None):
        return {}

    try:
        from weatherbrief.analysis.sounding import analyze_sounding_lite
        from weatherbrief.models.analysis import CloudCoverage

        sounding = analyze_sounding_lite(
            hourly.pressure_levels, hourly, model_key=model_key,
        )
        if sounding is None:
            return {}

        fields: dict[str, Any] = {}

        if sounding.indices:
            fields["sounding_ceiling_ft"] = sounding.indices.sounding_ceiling_ft
            fields["freezing_level_ft"] = sounding.indices.freezing_level_ft
            fields["sounding_cape_jkg"] = sounding.indices.cape_surface_jkg
            fields["sounding_cin_jkg"] = sounding.indices.cin_surface_jkg
            fields["sounding_lifted_index"] = sounding.indices.lifted_index

        # Lowest BKN/OVC cloud layer base → sounding_cloud_base_ft
        bkn_ovc = [
            cl for cl in sounding.dd_cloud_layers
            if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
        ]
        if bkn_ovc:
            lowest = min(bkn_ovc, key=lambda cl: cl.base_ft)
            fields["sounding_cloud_base_ft"] = lowest.base_ft

        # Model-native cloud-layer ceiling — the third ceiling estimate, so DD
        # vs layer vs the model's own diagnostic can be scored against METARs
        # later. `analyze_sounding_lite` does not build these (the heavy pass
        # does), so call the builder directly: it needs only the diagnostics,
        # the bulk covers and the pressure levels, which is far cheaper than
        # running the heavy pass over ~56K profiles a cycle.
        # Strictly additive: its own try/except and getattr defaults, so a
        # duck-typed `hourly` or a builder change can never take the *existing*
        # fields down with it. Without the inner guard an AttributeError here
        # hits the outer fail-silent handler and returns {} — blanking every
        # sounding column for that row.
        from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers

        try:
            nwp_layers = build_nwp_cloud_layers(
                nwp_cloud_diagnostics=getattr(hourly, "nwp_cloud_diagnostics", None),
                nwp_cloud_low_pct=getattr(hourly, "cloud_cover_low_pct", None),
                nwp_cloud_mid_pct=getattr(hourly, "cloud_cover_mid_pct", None),
                nwp_cloud_high_pct=getattr(hourly, "cloud_cover_high_pct", None),
                pressure_levels=getattr(hourly, "pressure_levels", None),
            )
        except Exception:
            logger.debug("Native cloud-layer ceiling unavailable", exc_info=True)
            nwp_layers = None
        if nwp_layers is not None:
            # A native source exists. Record which one, so a NULL ceiling here
            # reads as "no BKN/OVC deck" rather than "no layer data" — the two
            # resolve differently (the latter falls back to DD).
            srcs = {cl.source for cl in nwp_layers if cl.source}
            fields["nwp_layer_source"] = (
                next(iter(srcs)) if len(srcs) == 1 else (",".join(sorted(srcs))[:16] or "nwp")
            ) if srcs else "nwp"
            nwp_decks = [
                cl for cl in nwp_layers
                if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
            ]
            if nwp_decks:
                fields["nwp_layer_ceiling_ft"] = min(
                    nwp_decks, key=lambda cl: cl.base_ft
                ).base_ft

        if sounding.convective and sounding.convective.risk_level is not None:
            fields["sounding_convective_risk"] = sounding.convective.risk_level.value

        # EDR calibration harvest (issue #221) — inert today (the cycle no
        # longer instantiates an accumulator, and analyze_sounding_lite never
        # computes Richardson) but the plumbing is preserved for a future
        # subsampled design. NOTE: inline path only — batch payloads
        # (analyze_sounding_batch_items) deliberately don't carry an
        # accumulator, so a revived #221 must either add EDR data to the
        # batch result shape or route the calibration subsample inline.
        if edr_acc is not None and sounding.derived_levels:
            edr_acc.observe_richardson_levels(model_key, sounding.derived_levels)

        return fields

    except Exception:
        logger.warning(
            "Sounding analysis failed (%s), surface data preserved",
            model_key, exc_info=True,
        )
        return {}


def build_sounding_payload(hourly: "HourlyForecast", model_key: str) -> dict[str, Any]:
    """Serialise one profile to the plain-dict shape the pool worker accepts.

    ``surface`` excludes ``pressure_levels`` (they travel in ``levels``) so
    the worker can rebuild the exact ``HourlyForecast`` it would have seen
    inline.
    """
    return {
        "levels": [lvl.model_dump() for lvl in hourly.pressure_levels or []],
        "surface": hourly.model_dump(exclude={"pressure_levels"}),
        "model": model_key,
    }


def analyze_sounding_batch_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Analyse a batch of serialised profiles; one result dict per item.

    Runs inside a pool worker (dispatched as ``analyze_sounding_batch``) or
    inline when the pool is disabled. Per-item fail-silent: a malformed item
    yields ``{}`` without affecting its neighbours.
    """
    from weatherbrief.models.analysis import HourlyForecast

    results: list[dict[str, Any]] = []
    for item in items:
        try:
            hourly = HourlyForecast.model_validate({
                **item["surface"],
                "pressure_levels": item["levels"],
            })
            results.append(
                compute_snapshot_sounding_fields(hourly, item["model"])
            )
        except Exception:
            logger.warning("Sounding batch item failed", exc_info=True)
            results.append({})
    return results
