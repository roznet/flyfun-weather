"""DD-vs-NWP within-model agreement advisory.

Compares the thermodynamic (DD) and model-native (NWP) analysis tracks for
each model at each route point.  Disagreement between these two independent
derivations of the same conditions is a useful "model struggling" signal —
e.g. DD sees cirrus implied by RH while NWP ``cc`` reports clear sky
(ice supersaturation), or the sounding-derived freezing level differs from
the model's native ``deg0l`` by >2000 ft (vertical resolution artefact).

Default-disabled — intended as a calibration/diagnostic signal for the dev
team and digest context rather than a pilot-facing advisory.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    format_extent,
    pct_above_threshold,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    EnhancedCloudLayer,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


def _cloud_overlap_fraction(
    a: list[EnhancedCloudLayer],
    b: list[EnhancedCloudLayer],
) -> float:
    """Jaccard-like overlap on altitude intervals.

    Returns 1.0 when both lists are empty (mutually agreeing on no cloud).
    Returns ratio of intersection / union of altitude coverage otherwise.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    def _merge(layers: list[EnhancedCloudLayer]) -> list[tuple[float, float]]:
        """Collapse a model's layers into disjoint altitude spans.

        A single model's layer list can self-overlap: ``_build_grib_layers``
        emits the low/mid/high bands *and* a convective deck, and a convective
        deck routinely spans all three. Both the union and the intersection
        below assume disjoint spans, so we merge before measuring — otherwise
        the pairwise intersection double-counts and the fraction can exceed 1.
        """
        spans = sorted(
            (cl.base_ft, cl.top_ft) for cl in layers if cl.top_ft > cl.base_ft
        )
        merged: list[list[float]] = []
        for base, top in spans:
            if merged and base <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], top)
            else:
                merged.append([base, top])
        return [(base, top) for base, top in merged]

    def _length(spans: list[tuple[float, float]]) -> float:
        return sum(top - base for base, top in spans)

    def _intersect_length(
        sa: list[tuple[float, float]], sb: list[tuple[float, float]]
    ) -> float:
        total = 0.0
        for base_a, top_a in sa:
            for base_b, top_b in sb:
                lo = max(base_a, base_b)
                hi = min(top_a, top_b)
                if hi > lo:
                    total += hi - lo
        return total

    spans_a = _merge(a)
    spans_b = _merge(b)
    intersection = _intersect_length(spans_a, spans_b)
    union = _length(spans_a) + _length(spans_b) - intersection
    if union <= 0:
        return 1.0
    return intersection / union


@register
class DDvsNWPAgreementEvaluator:
    """Within-model agreement between thermodynamic (DD) and NWP analysis tracks."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="dd_nwp_agreement",
            name="DD vs NWP Agreement",
            short_description="Thermodynamic and model-native tracks agree",
            description=(
                "Compares the sounding-derived (DD) and model-native (NWP) "
                "analysis tracks for each model on freezing level and cloud "
                "overlap.  Disagreement between these two independent "
                "derivations flags conditions the model handles poorly. "
                "Convective DD-vs-NWP divergence is reported separately by the "
                "convective advisory's inline cross-check (#283)."
            ),
            category="model",
            default_enabled=False,
            parameters=[
                AdvisoryParameterDef(
                    key="freezing_delta_ft",
                    label="Freezing level delta",
                    description="Absolute DD−NWP freezing level delta considered a disagreement",
                    type="altitude",
                    unit="ft",
                    default=2000,
                    min=500,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="cloud_overlap_min",
                    label="Min cloud overlap",
                    description="Cloud layer Jaccard below this counts as disagreement",
                    type="percent",
                    unit="%",
                    default=30,
                    min=0,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="amber_pct",
                    label="Route % disagreeing for amber",
                    description="Minimum route percentage with disagreement to flag amber",
                    type="percent",
                    unit="%",
                    default=30,
                    min=5,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="red_pct",
                    label="Route % disagreeing for red",
                    description="Minimum route percentage with disagreement to flag red",
                    type="percent",
                    unit="%",
                    default=60,
                    min=10,
                    max=100,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        freezing_delta_ft = params.get("freezing_delta_ft", 2000)
        cloud_overlap_min = params.get("cloud_overlap_min", 30) / 100.0
        amber_pct = params.get("amber_pct", 30)
        red_pct = params.get("red_pct", 60)

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            total = 0
            disagree_points = 0
            categories_triggered: dict[str, int] = {
                "freezing": 0, "clouds": 0,
            }

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None or sounding.indices is None:
                    continue

                # Collect disagreements on this point
                disagreements: list[str] = []

                # Freezing level
                dd_fz = sounding.indices.freezing_level_ft
                nwp_fz = sounding.indices.nwp_freezing_level_ft
                if dd_fz is not None and nwp_fz is not None:
                    if abs(dd_fz - nwp_fz) >= freezing_delta_ft:
                        disagreements.append("freezing")

                # Cloud layers (DD vs NWP)
                dd_clouds = sounding.dd_cloud_layers
                nwp_clouds = sounding.nwp_cloud_layers or []
                # Only compare when NWP layers are genuinely model-native.
                # source="synthesized" is derived from the DD envelope →
                # comparison would be circular. "nwp_3d" (ECMWF cc / ICON clc)
                # and "grib" (GFS LCDC/MCDC/HCDC bulk bands) are both
                # independent of DD.
                nwp_native = [cl for cl in nwp_clouds
                              if cl.source in ("nwp_3d", "grib")]
                has_native_nwp = len(nwp_native) > 0 or (
                    nwp_clouds == [] and sounding.nwp_cloud_diagnostics is not None
                )
                if has_native_nwp:
                    overlap = _cloud_overlap_fraction(dd_clouds, nwp_native)
                    if overlap < cloud_overlap_min:
                        disagreements.append("clouds")

                # Convective divergence is intentionally NOT compared here. The
                # NWP convective track is now model-native (#283), so DD-vs-NWP
                # convective disagreement is reported by the richer, convective-
                # specific inline cross-check in analysis/advisories/convective.py
                # (convective_cross_check). Reporting it here too would
                # double-count the same divergence — so this advisory stays
                # focused on freezing-level + cloud-overlap (see
                # designs/advisories.md).

                # Only count points where at least one comparison was possible
                if dd_fz is None and nwp_fz is None and not has_native_nwp:
                    continue

                total += 1
                if disagreements:
                    disagree_points += 1
                    for cat in disagreements:
                        categories_triggered[cat] = categories_triggered.get(cat, 0) + 1

            if total == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "no comparable DD/NWP data"
            elif disagree_points == 0:
                status = AdvisoryStatus.GREEN
                detail = "DD and NWP tracks agree"
            else:
                status = pct_above_threshold(
                    disagree_points, total, amber_pct, red_pct,
                )
                ext = format_extent(disagree_points, total, ctx.total_distance_nm)
                top_cat = max(categories_triggered, key=categories_triggered.get)
                detail = f"{top_cat} track diverges over {ext}"

            per_model.append(ModelAdvisoryResult.build(
                model=model,
                status=status,
                detail=detail,
                affected=disagree_points,
                total=total,
                total_distance_nm=ctx.total_distance_nm,
            ))

        return RouteAdvisoryResult.from_per_model(
            "dd_nwp_agreement", per_model, params,
        )
