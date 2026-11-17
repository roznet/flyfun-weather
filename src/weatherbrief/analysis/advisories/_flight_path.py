"""Flight-path rollout helpers shared by the phase-1 cards (#1005).

``WB_FLIGHT_PATH=shadow`` (the default) keeps the cards grading flat cruise
and records, per model, what they would grade on the path as a
``ShadowGrade(rule="flight_path")`` on ``per_model[].shadow`` — the pattern of
``WB_SLD_ICING`` (§84). ``live`` grades on the path; ``off`` builds no path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.models import RouteAdvisoryResult, ShadowGrade

FLIGHT_PATH_SHADOW_RULE = "flight_path"


def path_shadow_ctx(ctx: RouteContext) -> RouteContext | None:
    """The same context graded on its path, when the path is only shadowed."""
    if ctx.flight_path is None or ctx.flight_path_live:
        return None
    return replace(ctx, flight_path_live=True)


def with_path_shadow(
    evaluate: Callable[[RouteContext, dict[str, float]], RouteAdvisoryResult],
    ctx: RouteContext,
    params: dict[str, float],
) -> RouteAdvisoryResult:
    """Run a card, and in shadow also run it on the path and record the difference."""
    result = evaluate(ctx, params)
    shadow_ctx = path_shadow_ctx(ctx)
    if shadow_ctx is None:
        return result
    on_path = evaluate(shadow_ctx, params)
    record_path_shadow(result, {m.model: (m.status, m.detail, m.affected_points) for m in on_path.per_model})
    return result


def record_path_shadow(
    result: RouteAdvisoryResult,
    by_model: dict[str, tuple],
) -> None:
    """Append the path grade ``(status, detail, points[, reason])`` to each model's shadow list."""
    for m in result.per_model:
        hit = by_model.get(m.model)
        if hit is None:
            continue
        status, detail, points = hit[0], hit[1], hit[2]
        reason = hit[3] if len(hit) > 3 and hit[3] else (
            "same" if status == m.status else "differs"
        )
        m.shadow.append(ShadowGrade(
            rule=FLIGHT_PATH_SHADOW_RULE, status=status, reason_code=reason,
            points=points, detail=detail,
        ))


def flown_band(ctx: RouteContext, idx: int) -> tuple[float, float] | None:
    """The altitude band the aircraft flies in ``analyses[idx]``'s cell, when the path is live.

    ``(base_ft, top_ft)`` over the cell's climb, level and descent parts; None
    in flat mode, where callers read ``ctx.cruise_altitude_ft``.
    """
    cell = ctx.path_slice(idx)
    if cell is None:
        return None
    lows = [p.base_ft for p in cell.vertical]
    highs = [p.top_ft for p in cell.vertical]
    if cell.level_nm > 0 or not cell.vertical:
        lows.append(cell.level_ft)
        highs.append(cell.level_ft)
    return min(lows), max(highs)


def path_rate_limit(ctx: RouteContext) -> tuple[float, float] | None:
    """The path's mean climb and descent gradients (ft per nm) for the solver, when live.

    ``vertical_profile.solve(rate_limit=...)`` then never proposes a climb or
    descent steeper than the aircraft flies its planned one (#1005).
    """
    path = ctx.flight_path
    if not ctx.flight_path_live or path is None:
        return None
    climb_nm = path.toc_nm
    descent_nm = path.total_distance_nm - path.tod_nm
    climb_ft = path.peak_ft - path.departure_elevation_ft
    descent_ft = path.peak_ft - path.arrival_elevation_ft
    climb_g = climb_ft / climb_nm if climb_nm > 0 and climb_ft > 0 else None
    descent_g = descent_ft / descent_nm if descent_nm > 0 and descent_ft > 0 else None
    if climb_g is None and descent_g is None:
        return None
    # A side with no gradient of its own (e.g. a field at cruise) takes the other's.
    return (climb_g or descent_g, descent_g or climb_g)


def in_cloud_on_path(ctx: RouteContext, cloud_layers_at) -> list[tuple[str, float, float, float]]:
    """Where the climb and descent fly in BKN / OVC cloud, from the path (#1005).

    ``cloud_layers_at(idx)`` returns the cloud layers at ``analyses[idx]`` (or
    None when the point is not assessed). Returns ``(phase, lo_nm, hi_nm,
    minutes)`` per phase with any cloud, minutes at the path's clean rate.
    Empty when the path is not live.
    """
    from weatherbrief.models import CloudCoverage

    found: dict[str, list[float]] = {}
    for idx in range(len(ctx.analyses)):
        cell = ctx.path_slice(idx)
        if cell is None:
            return []
        layers = cloud_layers_at(idx)
        if not layers:
            continue
        for part in cell.vertical:
            if part.fpm <= 0:
                continue
            thick = 0.0
            for cl in layers:
                if cl.coverage not in (CloudCoverage.BKN, CloudCoverage.OVC):
                    continue
                lo, hi = max(cl.base_ft, part.base_ft), min(cl.top_ft, part.top_ft)
                if hi > lo:
                    thick += hi - lo
            if thick <= 0:
                continue
            thick = min(thick, part.top_ft - part.base_ft)
            minutes = thick / part.fpm
            entry = found.get(part.phase)
            if entry is None:
                found[part.phase] = [part.lo_nm, part.hi_nm, minutes]
            else:
                entry[0] = min(entry[0], part.lo_nm)
                entry[1] = max(entry[1], part.hi_nm)
                entry[2] += minutes
    return [(ph, v[0], v[1], v[2]) for ph, v in found.items()]


def in_cloud_sentence(ctx: RouteContext, stretches, locale: str | None) -> str:
    """'; in cloud on the climb from nm 3 to nm 14, about 6 min' (empty when none)."""
    from weatherbrief.analysis.advisories.strings import adv_t

    parts = []
    total = ctx.total_distance_nm
    for phase, lo, hi, minutes in sorted(stretches, key=lambda s: s[1]):
        mins = max(1, round(minutes))
        if phase == "climb":
            parts.append(adv_t(
                "flight_path.in_cloud.climb", locale,
                lo=round(lo), hi=max(round(hi), round(lo) + 1), minutes=mins,
            ))
        else:
            out_far, out_near = total - lo, total - hi
            parts.append(adv_t(
                "flight_path.in_cloud.descent", locale,
                far=max(round(out_far), round(out_near) + 1), near=round(out_near), minutes=mins,
            ))
    return "".join(parts)
