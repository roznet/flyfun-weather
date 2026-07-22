"""ICON-D2 ceiling-limited fetch (#469 phase 2) — GATED OFF by default.

The wind/cloud sounding variables (u/v/w/qc/qi/clc) carry nothing useful above
the flight ceiling, so they are fetched only down to a domain-safe model-level
cut derived from the ceiling. The thermodynamic column (t/qv/p) stays full so
MetPy CAPE — the buoyancy integral to an equilibrium level far above the flight
— is unaffected.

That asymmetry is why the feature ships behind ``icon_ceiling_limit_enabled()``
with the default FALSE: it leaves pressure levels above the cut carrying
temperature but no wind, which the downstream consumers currently render as
REASSURING rather than unavailable (see the function's docstring). These tests
exercise the machinery directly / with the gate forced on so it stays correct
for re-landing, plus the gate's own default-off behaviour.

Covers the pure cut/level-split logic, the per-variable level plumbing on the
context, the prefetch honouring it, _prepare_icon_eu wiring the ceiling in, and
the gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import weatherbrief.fetch.grib as grib_mod
import weatherbrief.fetch.grib.icon_eu_fetch as icon_fetch_mod
from weatherbrief.fetch.grib import _IconEuContext
from weatherbrief.fetch.grib.cache import cache_key, get_cached
from weatherbrief.fetch.grib.icon_eu_fetch import (
    FULL_COLUMN_VARIABLES,
    ICON_D2,
    ICON_EU,
    LIMITED_LEVEL_VARIABLES,
    icon_ceiling_limit_enabled,
    icon_levels_by_var,
    icon_limited_top_level,
    icon_model_level_var_label,
)
from weatherbrief.models import ModelSource, RouteCrossSection, RoutePoint


def _rp(lat: float, lon: float, nm: float = 0.0) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=nm)


# ---------------------------------------------------------------------------
# Domain-safe level cut
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ceiling_ft, expected",
    [
        (18_000, 27),   # the issue's domain-safe cut for FL180
        (18_680, 27),   # exactly the anchor height still qualifies
        (18_681, 16),   # just above the deepest anchor → full column
        (16_000, 30),
        (12_000, 30),   # between anchors → rounds to the deeper level
        (10_000, 38),
        (6_000, 38),    # no shallower anchor → deepest documented cut
        (20_000, 16),   # above every anchor → full column (level_min)
    ],
)
def test_d2_top_level_for_ceiling(ceiling_ft, expected):
    assert icon_limited_top_level(ICON_D2, ceiling_ft) == expected


def test_top_level_unknown_ceiling_is_full_column():
    assert icon_limited_top_level(ICON_D2, None) == ICON_D2.level_min


def test_top_level_eu_never_limits():
    # ICON-EU has no per-level cache to top up from → always full column.
    assert icon_limited_top_level(ICON_EU, 6_000) == ICON_EU.level_min


# ---------------------------------------------------------------------------
# Per-variable level split
# ---------------------------------------------------------------------------


def _full_d2_levels() -> list[int]:
    return list(range(ICON_D2.level_min, ICON_D2.level_max + 1))


def test_levels_by_var_none_when_no_truncation():
    assert icon_levels_by_var(ICON_EU, 6_000, _full_d2_levels()) is None
    assert icon_levels_by_var(ICON_D2, None, _full_d2_levels()) is None
    assert icon_levels_by_var(ICON_D2, 20_000, _full_d2_levels()) is None


def test_levels_by_var_limits_only_wind_cloud_vars():
    mapping = icon_levels_by_var(ICON_D2, 18_000, _full_d2_levels())
    assert mapping is not None
    assert set(mapping.keys()) == set(LIMITED_LEVEL_VARIABLES)
    for var in FULL_COLUMN_VARIABLES:
        assert var not in mapping  # t/qv/p stay full via levels_for_var fallback
    for var, levels in mapping.items():
        assert levels == list(range(27, ICON_D2.level_max + 1))


def test_levels_by_var_and_full_column_disjoint():
    # No variable is both limited and full — the two tuples must not overlap.
    assert not (set(LIMITED_LEVEL_VARIABLES) & set(FULL_COLUMN_VARIABLES))


def test_context_levels_for_var_splits_full_and_limited():
    full = _full_d2_levels()
    ctx = _IconEuContext(
        init_date="20260721", init_hour=0, forecast_hours=[12],
        run_dir=Path("/tmp"), levels=full,
        levels_by_var=icon_levels_by_var(ICON_D2, 18_000, full),
        point_lats=[48.0], point_lons=[11.0], session=None, variant=ICON_D2,
    )
    assert ctx.levels_for_var("t") == full           # full column
    assert ctx.levels_for_var("p") == full
    assert ctx.levels_for_var("u") == list(range(27, 66))  # limited
    assert ctx.levels_for_var("clc") == list(range(27, 66))


# ---------------------------------------------------------------------------
# Prefetch honours the per-variable cut
# ---------------------------------------------------------------------------


def test_prefetch_fetches_reduced_levels_for_limited_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "1")
    per_level_calls: list[tuple[str, tuple[int, ...]]] = []

    def fake_per_level(init_date, init_hour, fhour, levels, variables,
                       session=None, max_workers=8, variant=ICON_D2):
        (var,) = variables
        per_level_calls.append((var, tuple(sorted(levels))))
        return {(var, level): b"x" for level in levels}

    def fake_single(init_date, init_hour, fhours, variables=None,
                    session=None, max_workers=8, variant=ICON_D2):
        return {fhours[0]: b"d"}

    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_level", fake_per_level)
    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_single_level", fake_single)

    full = _full_d2_levels()
    ctx = _IconEuContext(
        init_date="20260721", init_hour=0, forecast_hours=[12],
        run_dir=tmp_path, levels=full,
        levels_by_var=icon_levels_by_var(ICON_D2, 18_000, full),
        point_lats=[48.0], point_lons=[11.0], session=None, variant=ICON_D2,
    )
    grib_mod._prefetch_icon_eu_data_inner(ctx)

    by_var = dict(per_level_calls)
    # Wind/cloud variables fetched only from the FL180 cut (level 27) down.
    for var in LIMITED_LEVEL_VARIABLES:
        assert by_var[var] == tuple(range(27, 66))
    # Thermodynamic column stays full.
    for var in FULL_COLUMN_VARIABLES:
        assert by_var[var] == tuple(range(16, 66))
    # And the reduced level files really landed on disk for a limited var.
    assert get_cached(tmp_path, cache_key(12, icon_model_level_var_label(ICON_D2, "u", 27))) == b"x"
    assert get_cached(tmp_path, cache_key(12, icon_model_level_var_label(ICON_D2, "u", 16))) is None


# ---------------------------------------------------------------------------
# _prepare_icon_eu wires the ceiling into the context
# ---------------------------------------------------------------------------


class TestPrepareCeiling:
    def _icon_sections(self):
        return [RouteCrossSection(
            model=ModelSource.ICON, route_points=[],
            fetched_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            point_forecasts=[],
        )]

    def _finder(self, d2_run):
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2 as _D2

        def _fake(target_time, session=None, as_of_time=None,
                  cover_until=None, variant=None):
            return d2_run if variant is _D2 else None
        return _fake

    def _prepare(self, tmp_path, ceiling, enabled=True):
        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]  # inside D2 bbox
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._finder(d2_run=("20260720", 12)),
        ), patch.object(grib_mod, "_d2_corridor_mask_ok", return_value=True), \
                patch.object(
                    # _prepare_icon_eu imports this function-locally, so it
                    # resolves from the source module at call time.
                    icon_fetch_mod, "icon_ceiling_limit_enabled",
                    return_value=enabled,
                ):
            ctx, skip = grib_mod._prepare_icon_eu(
                self._icon_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
                flight_ceiling_ft=ceiling,
            )
        return ctx

    def test_low_ceiling_limits_d2_context(self, tmp_path):
        ctx = self._prepare(tmp_path, ceiling=18_000)
        assert ctx is not None and ctx.variant is ICON_D2
        assert ctx.levels_by_var is not None
        assert ctx.levels_for_var("u") == list(range(27, 66))
        assert ctx.levels_for_var("t") == list(range(16, 66))

    def test_high_ceiling_keeps_full_column(self, tmp_path):
        ctx = self._prepare(tmp_path, ceiling=20_000)
        assert ctx is not None and ctx.variant is ICON_D2
        assert ctx.levels_by_var is None  # no truncation

    def test_gate_off_keeps_full_column_even_at_low_ceiling(self, tmp_path):
        # #469 phase 2 is gated OFF by default: a truncated column reads as
        # reassuring downstream (turbulence GREEN not UNAVAILABLE, high cloud
        # decks as "clear"). With the gate off, a ceiling that WOULD cut must
        # still yield the full column for every variable.
        ctx = self._prepare(tmp_path, ceiling=18_000, enabled=False)
        assert ctx is not None and ctx.variant is ICON_D2
        assert ctx.levels_by_var is None
        assert ctx.levels_for_var("u") == list(range(16, 66))
        assert ctx.levels_for_var("t") == list(range(16, 66))


class TestCeilingLimitGate:
    """The env gate itself (#469 phase 2 held back pending consumer fixes)."""

    def test_enabled_by_default(self, monkeypatch):
        # #474 re-landed the cut with the consumer fixes → default ON.
        monkeypatch.delenv("WB_ICON_CEILING_LIMIT_ENABLED", raising=False)
        assert icon_ceiling_limit_enabled() is True

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("1", True), ("yes", True), ("TRUE", True),
        # An explicitly empty / unrecognised value disables (only the default,
        # i.e. an UNSET var, is now ON — see test_enabled_by_default).
        ("false", False), ("0", False), ("", False), ("garbage", False),
    ])
    def test_env_override(self, monkeypatch, raw, expected):
        monkeypatch.setenv("WB_ICON_CEILING_LIMIT_ENABLED", raw)
        assert icon_ceiling_limit_enabled() is expected
