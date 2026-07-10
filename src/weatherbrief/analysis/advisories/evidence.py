"""Shared advisory evidence, route geometry, and method provenance helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Literal, Sequence

from weatherbrief.models import (
    AdvisoryEvidenceRegion,
    AdvisoryStatus,
    Mitigation,
    ModelAdvisoryResult,
    RoutePointAnalysis,
    SoundingAnalysis,
)

DataState = Literal["complete", "partial", "unavailable"]

_EVIDENCE_SEVERITY_ORDER = {
    AdvisoryStatus.GREEN: 0,
    AdvisoryStatus.AMBER: 1,
    AdvisoryStatus.RED: 2,
    AdvisoryStatus.UNAVAILABLE: 3,
}


@dataclass(frozen=True)
class EvidenceSample:
    """One point-level contribution to an advisory's display evidence."""

    point_index: int
    severity: AdvisoryStatus
    reason_code: str
    metric_id: str | None = None
    method_id: str | None = None
    lower_altitude_ft: int | None = None
    upper_altitude_ft: int | None = None


@dataclass(frozen=True)
class EvidenceSummary:
    """Geometry, coverage, and data-state derived from point assessments."""

    data_state: DataState
    affected_points: int
    total_points: int
    affected_pct: float
    affected_nm: float
    total_nm: float
    affected_mod_points: int
    affected_mod_pct: float
    affected_mod_nm: float
    evidence_regions: list[AdvisoryEvidenceRegion]

    def format_extent(self) -> str:
        return (
            f"{round(self.affected_nm)}nm/{round(self.total_nm)}nm "
            f"({self.affected_pct:.0f}%)"
        )

    def format_mod_extent(self) -> str:
        return (
            f"{round(self.affected_mod_nm)}nm/{round(self.total_nm)}nm "
            f"({self.affected_mod_pct:.0f}%)"
        )

    def build_result(
        self,
        *,
        model: str,
        status: AdvisoryStatus,
        detail: str,
        unavailable_detail: str,
        primary_method_id: str | None,
        cross_check: str | None = None,
        mitigations: list[Mitigation] | None = None,
    ) -> ModelAdvisoryResult:
        guarded = guard_status_for_data_state(status, self.data_state)
        return ModelAdvisoryResult(
            model=model,
            status=guarded,
            detail=unavailable_detail if guarded == AdvisoryStatus.UNAVAILABLE else detail,
            affected_points=self.affected_points,
            total_points=self.total_points,
            affected_pct=self.affected_pct,
            affected_nm=self.affected_nm,
            total_nm=self.total_nm,
            affected_mod_points=self.affected_mod_points,
            affected_mod_pct=self.affected_mod_pct,
            affected_mod_nm=self.affected_mod_nm,
            cross_check=cross_check,
            mitigations=mitigations or [],
            data_state=self.data_state,
            primary_method_id=primary_method_id,
            evidence_regions=self.evidence_regions,
        )


def data_state_from_domains(
    *,
    expected: Collection[object],
    evaluated: Collection[object],
    complete: Collection[object],
) -> DataState:
    """Classify coverage of an expected evaluation domain."""
    expected_set = set(expected)
    evaluated_set = set(evaluated) & expected_set
    complete_set = set(complete) & evaluated_set
    if not expected_set or not evaluated_set:
        return "unavailable"
    if complete_set == expected_set:
        return "complete"
    return "partial"


def combine_data_states(*states: DataState) -> DataState:
    """Combine independent domain states without losing missing-data warnings."""
    if states and all(state == "complete" for state in states):
        return "complete"
    if any(state in ("complete", "partial") for state in states):
        return "partial"
    return "unavailable"


def guard_status_for_data_state(
    status: AdvisoryStatus,
    data_state: DataState,
) -> AdvisoryStatus:
    """Prevent unavailable or partial-clear data from presenting as GREEN."""
    if data_state == "unavailable":
        return AdvisoryStatus.UNAVAILABLE
    if data_state == "partial" and status == AdvisoryStatus.GREEN:
        return AdvisoryStatus.UNAVAILABLE
    return status


def summarize_evidence(
    *,
    route_points: Sequence[RoutePointAnalysis],
    total_distance_nm: float,
    evaluated_point_indices: Collection[int],
    complete_point_indices: Collection[int],
    affected_point_indices: Collection[int],
    evidence_samples: Sequence[EvidenceSample],
    moderate_point_indices: Collection[int] = (),
) -> EvidenceSummary:
    """Derive unique point extents and contiguous evidence regions."""
    ordered = sorted(
        route_points,
        key=lambda point: (point.distance_from_origin_nm, point.point_index),
    )
    indices = [point.point_index for point in ordered]
    if len(indices) != len(set(indices)):
        raise ValueError("route point indices must be unique")
    if any(previous >= following for previous, following in zip(indices, indices[1:])):
        raise ValueError("route point indices must increase in route order")

    expected = set(indices)
    evaluated = set(evaluated_point_indices) & expected
    complete = set(complete_point_indices) & evaluated
    affected = set(affected_point_indices) & evaluated
    moderate = set(moderate_point_indices) & evaluated
    position_by_index = {
        point.point_index: position for position, point in enumerate(ordered)
    }

    cells: dict[int, tuple[float, float]] = {}
    for position, point in enumerate(ordered):
        distance = point.distance_from_origin_nm
        start = (
            0.0
            if position == 0
            else (ordered[position - 1].distance_from_origin_nm + distance) / 2.0
        )
        end = (
            total_distance_nm
            if position == len(ordered) - 1
            else (distance + ordered[position + 1].distance_from_origin_nm) / 2.0
        )
        cells[point.point_index] = (
            max(0.0, min(total_distance_nm, start)),
            max(0.0, min(total_distance_nm, end)),
        )

    def distance_for(point_indices: set[int]) -> float:
        return round(
            sum(cells[index][1] - cells[index][0] for index in point_indices),
            1,
        )

    grouped: dict[
        tuple[
            AdvisoryStatus,
            str,
            str | None,
            str | None,
            int | None,
            int | None,
        ],
        set[int],
    ] = {}
    for sample in set(evidence_samples):
        if (
            sample.point_index not in evaluated
            or sample.severity == AdvisoryStatus.UNAVAILABLE
        ):
            continue
        key = (
            sample.severity,
            sample.reason_code,
            sample.metric_id,
            sample.method_id,
            sample.lower_altitude_ft,
            sample.upper_altitude_ft,
        )
        grouped.setdefault(key, set()).add(position_by_index[sample.point_index])

    regions_with_position: list[tuple[int, AdvisoryEvidenceRegion]] = []
    for key, positions in grouped.items():
        severity, reason, metric, method, lower, upper = key
        run_start: int | None = None
        run_end: int | None = None
        for position in sorted(positions):
            if run_start is None:
                run_start = run_end = position
                continue
            if (
                position == run_end + 1
                and ordered[position].point_index
                == ordered[run_end].point_index + 1
            ):
                run_end = position
                continue
            regions_with_position.append(
                (
                    run_start,
                    AdvisoryEvidenceRegion(
                        start_point_index=ordered[run_start].point_index,
                        end_point_index=ordered[run_end].point_index,
                        lower_altitude_ft=lower,
                        upper_altitude_ft=upper,
                        severity=severity,
                        reason_code=reason,
                        metric_id=metric,
                        method_id=method,
                    ),
                )
            )
            run_start = run_end = position
        if run_start is not None and run_end is not None:
            regions_with_position.append(
                (
                    run_start,
                    AdvisoryEvidenceRegion(
                        start_point_index=ordered[run_start].point_index,
                        end_point_index=ordered[run_end].point_index,
                        lower_altitude_ft=lower,
                        upper_altitude_ft=upper,
                        severity=severity,
                        reason_code=reason,
                        metric_id=metric,
                        method_id=method,
                    ),
                )
            )

    total_points = len(evaluated)
    affected_points = len(affected)
    affected_mod_points = len(moderate)

    def region_sort_key(
        item: tuple[int, AdvisoryEvidenceRegion],
    ) -> tuple[object, ...]:
        position, region = item
        lower = region.lower_altitude_ft
        upper = region.upper_altitude_ft
        metric = region.metric_id
        method = region.method_id
        return (
            position,
            region.reason_code,
            -1 if lower is None else lower,
            lower is not None,
            region.end_point_index,
            upper is not None,
            0 if upper is None else upper,
            _EVIDENCE_SEVERITY_ORDER[region.severity],
            metric is not None,
            "" if metric is None else metric,
            method is not None,
            "" if method is None else method,
        )

    return EvidenceSummary(
        data_state=data_state_from_domains(
            expected=expected,
            evaluated=evaluated,
            complete=complete,
        ),
        affected_points=affected_points,
        total_points=total_points,
        affected_pct=(
            round(100 * affected_points / total_points, 1) if total_points else 0.0
        ),
        affected_nm=distance_for(affected),
        total_nm=round(total_distance_nm, 1),
        affected_mod_points=affected_mod_points,
        affected_mod_pct=(
            round(100 * affected_mod_points / total_points, 1)
            if total_points
            else 0.0
        ),
        affected_mod_nm=distance_for(moderate),
        evidence_regions=[
            region
            for _, region in sorted(regions_with_position, key=region_sort_key)
        ],
    )


def build_non_spatial_result(
    *,
    model: str,
    status: AdvisoryStatus,
    detail: str,
    unavailable_detail: str,
    expected_entities: Collection[str],
    evaluated_entities: Collection[str],
    complete_entities: Collection[str],
    affected_entities: Collection[str],
    primary_method_id: str | None,
) -> ModelAdvisoryResult:
    """Build a guarded result for entity domains without route geometry."""
    expected = set(expected_entities)
    evaluated = set(evaluated_entities) & expected
    affected = set(affected_entities) & evaluated
    state = data_state_from_domains(
        expected=expected,
        evaluated=evaluated,
        complete=set(complete_entities) & expected,
    )
    guarded = guard_status_for_data_state(status, state)
    return ModelAdvisoryResult(
        model=model,
        status=guarded,
        detail=unavailable_detail if guarded == AdvisoryStatus.UNAVAILABLE else detail,
        affected_points=len(affected),
        total_points=len(evaluated),
        affected_pct=(
            round(100 * len(affected) / len(evaluated), 1) if evaluated else 0.0
        ),
        affected_nm=0.0,
        total_nm=0.0,
        data_state=state,
        primary_method_id=primary_method_id,
    )


def cloud_method_id(
    effective: str | None,
    requested: str | None = None,
) -> str | None:
    """Normalize effective cloud provenance without guessing unknown methods."""
    if effective == "dd":
        return "dewpoint_depression"
    if effective == "nwp":
        return "nwp"
    if effective == "nwp_synthesized":
        return "nwp_synthesized"
    if effective is None and (
        requested is None or requested == "dd" or requested.endswith("_dd")
    ):
        return "dewpoint_depression"
    return None


def icing_method_id(value: str | None) -> str | None:
    """Normalize requested icing-method identifiers for advisory provenance."""
    if value is None:
        return "ogimet_dd"
    return {
        "ogimet_dd": "ogimet_dd",
        "ogimet_nwp": "ogimet_nwp",
        "sfip_nwp": "sfip",
        "ieng": "ieng",
    }.get(value)


def icing_method_is_available(
    sounding: SoundingAnalysis | None,
    value: str | None,
) -> bool:
    """Return whether the selected icing method has its required geometry."""
    if sounding is None:
        return False
    if value in ("ogimet_nwp", "ieng"):
        return sounding.nwp_cloud_layers is not None
    return value in (None, "ogimet_dd", "sfip_nwp")


def convective_method_id(value: str | None) -> str | None:
    """Normalize requested convective-method identifiers without guessing."""
    if value is None or value == "thermo":
        return "thermo"
    if value == "nwp":
        return "nwp"
    return None
