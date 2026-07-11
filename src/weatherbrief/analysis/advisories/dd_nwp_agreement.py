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

import math
from dataclasses import replace

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    pct_above_threshold,
)
from weatherbrief.analysis.advisories.evidence import EvidenceSample, summarize_evidence
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    EnhancedCloudLayer,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)


def _merge_spans(layers: list[EnhancedCloudLayer]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    finite_spans = [
        (cl.base_ft, cl.top_ft)
        for cl in layers
        if math.isfinite(cl.base_ft) and math.isfinite(cl.top_ft)
    ]
    for base, top in sorted(finite_spans):
        if top <= base:
            continue
        if merged and base <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], top)
        else:
            merged.append([base, top])
    return [(base, top) for base, top in merged]


def _span_length(spans: list[tuple[float, float]]) -> float:
    return sum(top - base for base, top in spans)


def _intersection_length(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> float:
    total = 0.0
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _cloud_overlap_fraction(
    a: list[EnhancedCloudLayer],
    b: list[EnhancedCloudLayer],
) -> float:
    """Jaccard-like overlap on altitude intervals.

    Returns 1.0 when both lists are empty (mutually agreeing on no cloud).
    Returns ratio of intersection / union of altitude coverage otherwise.
    """
    a_spans = _merge_spans(a)
    b_spans = _merge_spans(b)
    if not a_spans and not b_spans:
        return 1.0
    if not a_spans or not b_spans:
        return 0.0

    intersection = _intersection_length(a_spans, b_spans)
    union = _span_length(a_spans) + _span_length(b_spans) - intersection
    if union <= 0:
        return 1.0
    return max(0.0, min(1.0, intersection / union))


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
            evaluated: set[int] = set()
            complete: set[int] = set()
            disagreement_points: set[int] = set()
            samples: list[EvidenceSample] = []
            categories_triggered: dict[str, int] = {
                "freezing": 0, "clouds": 0,
            }

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue

                # Collect disagreements on this point
                disagreements: list[str] = []

                # Freezing level
                indices = sounding.indices
                dd_fz = indices.freezing_level_ft if indices is not None else None
                nwp_fz = (
                    indices.nwp_freezing_level_ft if indices is not None else None
                )
                freezing_comparable = dd_fz is not None and nwp_fz is not None
                if freezing_comparable:
                    if abs(dd_fz - nwp_fz) >= freezing_delta_ft:
                        disagreements.append("freezing")
                        samples.append(
                            EvidenceSample(
                                point_index=rpa.point_index,
                                severity=AdvisoryStatus.AMBER,
                                reason_code="freezing_level_disagreement",
                                metric_id="freezing_level_ft",
                                method_id="dd_vs_nwp",
                                lower_altitude_ft=round(min(dd_fz, nwp_fz)),
                                upper_altitude_ft=round(max(dd_fz, nwp_fz)),
                            )
                        )

                # Cloud layers (DD vs NWP)
                dd_clouds = sounding.dd_cloud_layers
                nwp_clouds = sounding.nwp_cloud_layers
                # Only compare when NWP layers are genuinely model-native.
                # source="synthesized" is derived from the DD envelope →
                # comparison would be circular. "nwp_3d" (ECMWF cc / ICON clc)
                # and "grib" (GFS LCDC/MCDC/HCDC bulk bands) are both
                # independent of DD.
                nwp_native = [
                    layer
                    for layer in (nwp_clouds or [])
                    if layer.source in ("nwp_3d", "grib")
                ]
                has_native_nwp = nwp_clouds is not None and (
                    len(nwp_native) > 0
                    or (
                        nwp_clouds == []
                        and sounding.nwp_cloud_diagnostics is not None
                    )
                )
                if has_native_nwp:
                    overlap = _cloud_overlap_fraction(dd_clouds, nwp_native)
                    if overlap < cloud_overlap_min:
                        disagreements.append("clouds")
                        samples.extend(
                            EvidenceSample(
                                point_index=rpa.point_index,
                                severity=AdvisoryStatus.AMBER,
                                reason_code="dd_cloud_disagreement",
                                metric_id="cloud_coverage",
                                method_id="dewpoint_depression",
                                lower_altitude_ft=round(base_ft),
                                upper_altitude_ft=round(top_ft),
                            )
                            for base_ft, top_ft in _merge_spans(dd_clouds)
                        )
                        samples.extend(
                            EvidenceSample(
                                point_index=rpa.point_index,
                                severity=AdvisoryStatus.AMBER,
                                reason_code="nwp_cloud_disagreement",
                                metric_id="cloud_coverage",
                                method_id="nwp",
                                lower_altitude_ft=round(base_ft),
                                upper_altitude_ft=round(top_ft),
                            )
                            for base_ft, top_ft in _merge_spans(nwp_native)
                        )

                # Convective divergence is intentionally NOT compared here. The
                # NWP convective track is now model-native (#283), so DD-vs-NWP
                # convective disagreement is reported by the richer, convective-
                # specific inline cross-check in analysis/advisories/convective.py
                # (convective_cross_check). Reporting it here too would
                # double-count the same divergence — so this advisory stays
                # focused on freezing-level + cloud-overlap (see
                # designs/advisories.md).

                # Only count points where one of the exact grading comparisons
                # had both of its required inputs.
                if not freezing_comparable and not has_native_nwp:
                    continue

                evaluated.add(rpa.point_index)
                if freezing_comparable and has_native_nwp:
                    complete.add(rpa.point_index)
                if disagreements:
                    disagreement_points.add(rpa.point_index)
                    for cat in disagreements:
                        categories_triggered[cat] = categories_triggered.get(cat, 0) + 1

            summary = summarize_evidence(
                route_points=ctx.analyses,
                total_distance_nm=ctx.total_distance_nm,
                evaluated_point_indices=evaluated,
                complete_point_indices=complete,
                affected_point_indices=disagreement_points,
                evidence_samples=samples,
            )

            if summary.total_points == 0:
                status = AdvisoryStatus.UNAVAILABLE
                detail = "no comparable DD/NWP data"
            elif summary.affected_points == 0:
                status = AdvisoryStatus.GREEN
                detail = "DD and NWP tracks agree"
            else:
                status = pct_above_threshold(
                    summary.affected_points,
                    summary.total_points,
                    amber_pct,
                    red_pct,
                )
                ext = summary.format_extent()
                top_cat = max(categories_triggered, key=categories_triggered.get)
                detail = f"{top_cat} track diverges over {ext}"

            summary = replace(
                summary,
                evidence_regions=[
                    region.model_copy(update={"severity": status})
                    for region in summary.evidence_regions
                ],
            )
            missing_detail = adv_t(
                "no_data" if summary.data_state == "unavailable" else "partial_data",
                ctx.locale,
            )
            per_model.append(
                summary.build_result(
                    model=model,
                    status=status,
                    detail=detail,
                    unavailable_detail=missing_detail,
                    primary_method_id="dd_vs_nwp",
                )
            )

        return RouteAdvisoryResult.from_per_model(
            "dd_nwp_agreement", per_model, params,
        )
