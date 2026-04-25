"""Tests for weatherbrief.hewson.era5_case.build_synoptic_from_case.

Builds a synthetic ERA5 Case in tmp_path (single 850 hPa level, mirroring
the on-disk Ciarán case shape), runs the builder, and verifies the
resulting NPZ matches the synoptic-snapshot schema consumed by the
/api/hewson-map endpoints + the SynopticMap frontend.

build_terrain_mask is monkey-patched: it depends on SRTM data which we
don't want in unit tests, and it isn't part of what this module is
testing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.frontal.case import save_case_meta, save_model_fields


# ---------------------------------------------------------------------------
# Synthetic Case builder
# ---------------------------------------------------------------------------


# Small grid keeps tests fast — large enough that the gradient/Laplacian
# stencils have valid interior points.
_LAT = np.linspace(40.0, 50.0, 11)
_LON = np.linspace(-5.0, 5.0, 11)
_INIT = datetime(2023, 11, 2, 0, 0, 0, tzinfo=timezone.utc)


def _build_synthetic_era5_case(case_dir: Path, n_time: int = 4) -> None:
    """Write a Case directory with ``n_time`` 6-hourly steps at 850 hPa."""
    valid_times = np.array(
        [np.datetime64(_INIT.replace(tzinfo=None) + timedelta(hours=6 * i))
         for i in range(n_time)],
        dtype="datetime64[ns]",
    )

    fields_by_time = []
    for i in range(n_time):
        # Linear-in-x θe ramp so gradient is non-zero (and easy to predict).
        lon2d, lat2d = np.meshgrid(_LON, _LAT)
        theta_e = 290.0 + 0.5 * lon2d + 0.1 * i  # rises over time too
        T = theta_e - 5.0
        Td = theta_e - 8.0
        u = np.full_like(theta_e, 10.0)   # 10 m/s westerly
        v = np.zeros_like(theta_e)
        fields_by_time.append({
            "T850": T, "Td850": Td, "theta_e": theta_e,
            "u850": u, "v850": v,
        })

    save_case_meta(
        case_dir,
        case_name="2023-11-02_synthetic_era5",
        source="era5",
        lat=_LAT, lon=_LON,
        models=["era5"],
        init_times={"era5": 0},
    )
    save_model_fields(case_dir, "era5", fields_by_time, valid_times)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_terrain_mask(monkeypatch):
    """Replace build_terrain_mask with a synthetic all-valid mask so tests
    don't depend on SRTM data being available. Convention (per
    frontal/grid.py): True = below 1500m = valid."""
    def _fake_mask(lat, lon):
        return np.ones((len(lat), len(lon)), dtype=bool)
    monkeypatch.setattr(
        "weatherbrief.hewson.era5_case.build_terrain_mask",
        _fake_mask,
    )


def test_writes_snapshot_at_init_named_path(tmp_path, patched_terrain_mask):
    """Output filename is the ISO Z timestamp of the first valid time —
    matches the live precompute path convention so the slice endpoint can
    find it via the same (model, init) lookup."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "ciaran"
    _build_synthetic_era5_case(case_dir)

    out_path = build_synoptic_from_case(
        case_dir, output_dir=tmp_path / "hewson",
    )

    assert out_path.exists()
    assert out_path.parent.name == "era5"
    assert out_path.name == "2023-11-02T00:00:00Z.npz"


def test_snapshot_schema(tmp_path, patched_terrain_mask):
    """NPZ contains all the keys the /api/hewson-map endpoints expect:
    levels, stride_hours, init_time_unix, lat/lon, valid_times, and one
    (n_time, n_lat, n_lon) stack per (metric, level) combination."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "case"
    _build_synthetic_era5_case(case_dir, n_time=4)

    out_path = build_synoptic_from_case(case_dir, output_dir=tmp_path / "hewson")

    with np.load(out_path) as npz:
        # Required scalar / coord arrays
        assert int(npz["init_time_unix"]) == int(_INIT.timestamp())
        assert npz["levels"].tolist() == [850]
        assert int(npz["stride_hours"]) == 6
        assert npz["lat"].tolist() == _LAT.tolist()
        assert npz["lon"].tolist() == _LON.tolist()
        assert npz["valid_times"].shape == (4,)

        # All 6 metric stacks at 850 hPa with the right shape
        for metric in ("theta_e", "gradient", "neg_laplacian", "tfp",
                       "advection", "tendency"):
            arr = npz[f"{metric}_850"]
            assert arr.shape == (4, len(_LAT), len(_LON))
            assert arr.dtype == np.float32


def test_tendency_uses_stride_hours(tmp_path, patched_terrain_mask):
    """Tendency in K/h must come out independent of stride. Our θe ramps
    by 0.1 K per index (i.e. 0.1 K per 6 h step) → ∂θe/∂t ≈ 1/60 K/h.
    Allow some slack for edge effects and interior points."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "case"
    _build_synthetic_era5_case(case_dir, n_time=4)

    out_path = build_synoptic_from_case(case_dir, output_dir=tmp_path / "hewson")
    with np.load(out_path) as npz:
        tend = npz["tendency_850"]
        # Interior cell, interior time — should be the centred-diff value
        # (theta_e_at_t+1 - theta_e_at_t-1) / (2 * stride_hours)
        # = (0.1 - (-0.1)) / 12 ≈ 0.01667
        v = float(tend[1, 5, 5])
        assert v == pytest.approx(0.0167, abs=1e-3)


def test_single_timestep_tendency_is_nan(tmp_path, patched_terrain_mask):
    """ERA5 cases with only one analysis stamp produce NaN tendency. The
    builder must not raise; downstream code treats NaN as 'unavailable'."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "case"
    _build_synthetic_era5_case(case_dir, n_time=1)

    out_path = build_synoptic_from_case(case_dir, output_dir=tmp_path / "hewson")
    with np.load(out_path) as npz:
        # Other metrics should still be finite at interior points.
        theta_e = npz["theta_e_850"]
        assert np.isfinite(theta_e[0, 5, 5])
        # Tendency requires >= 2 timesteps — all-NaN.
        assert np.all(np.isnan(npz["tendency_850"]))
        # stride_hours falls back to the safe default (6) for single-step.
        assert int(npz["stride_hours"]) == 6


def test_rejects_non_era5_case(tmp_path, patched_terrain_mask):
    """The builder is ERA5-only; an Open-Meteo case should be rejected
    with a clear message rather than producing nonsense."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "om_case"
    valid_times = np.array(
        [np.datetime64(_INIT.replace(tzinfo=None) + timedelta(hours=h))
         for h in (0, 6)],
        dtype="datetime64[ns]",
    )
    fields = [{
        "T850": np.zeros((len(_LAT), len(_LON))),
        "Td850": np.zeros((len(_LAT), len(_LON))),
        "theta_e": np.zeros((len(_LAT), len(_LON))),
        "u850": np.zeros((len(_LAT), len(_LON))),
        "v850": np.zeros((len(_LAT), len(_LON))),
    } for _ in range(2)]
    save_case_meta(
        case_dir,
        case_name="om", source="open_meteo",
        lat=_LAT, lon=_LON,
        models=["ecmwf"],
        init_times={"ecmwf": int(_INIT.timestamp())},
    )
    save_model_fields(case_dir, "ecmwf", fields, valid_times)

    with pytest.raises(ValueError, match="era5"):
        build_synoptic_from_case(case_dir, output_dir=tmp_path / "hewson")


def test_levels_subset(tmp_path, patched_terrain_mask):
    """When ``levels=`` restricts to a subset of what's in the case, only
    those levels appear in the output. Asking for a missing level raises."""
    from weatherbrief.hewson.era5_case import build_synoptic_from_case

    case_dir = tmp_path / "case"
    _build_synthetic_era5_case(case_dir, n_time=2)

    # 850 is in the case → OK
    out_path = build_synoptic_from_case(
        case_dir, output_dir=tmp_path / "hewson", levels=[850],
    )
    with np.load(out_path) as npz:
        assert npz["levels"].tolist() == [850]

    # 700 is not in the case → ValueError
    with pytest.raises(ValueError, match="700"):
        build_synoptic_from_case(
            case_dir, output_dir=tmp_path / "hewson", levels=[700],
        )
