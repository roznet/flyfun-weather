"""Hewson grid from GRIB (#911): lattice extraction, run resolution, run_once.

Synthetic GRIB written with eccodes (``tests/grib_synth.py``) stands in for the
three products. Fields are linear in lat/lon, so an exact slice/subsample is
checked in closed form: a value from the wrong cell, a flipped axis or a
longitude off by 360° shows up as a numeric mismatch, not a near-miss.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

pytest.importorskip("eccodes")

from grib_synth import EUROPE, EUROPE_G2, NORDIC, US_G1, linear_field, write  # noqa: E402
from weatherbrief.frontal.grid import build_grid_coords
from weatherbrief.hewson import grib_source
from weatherbrief.hewson.grib_source import (
    GribRun,
    dewpoint_magnus_c,
    forecast_step_hours,
    lattice_indices,
    level_inputs,
    read_lattice_fields,
)
from weatherbrief.hewson.precompute import (
    DEFAULT_LEVELS,
    SOURCE_GRIB,
    load_snapshot,
    run_once,
    snapshot_path,
)

# Per-variable offsets so a t/r/u/v mix-up is caught too; T ≈ 267-282 K and
# RH ≈ 20-60 % on the grid, so θe stays physical.
_OFFSETS = {"t": 235.0, "r": 0.0, "u": 5.0, "v": -3.0}
_LEVEL_OFFSET = {925: 0.0, 850: -5.0, 700: -15.0}


def _expected(var: str, level: int, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    return linear_field(lat, lon, offset=_OFFSETS[var] + _LEVEL_OFFSET[level])


def _grib(areas, *, levels=DEFAULT_LEVELS, step=0.25, sample="regular_ll_pl_grib2",
          j_positive=False, skip=()) -> bytes:
    """One step's GRIB: t/r/u/v at ``levels`` on every area, linear fields."""
    buf = io.BytesIO()
    for area in areas:
        for var, off in _OFFSETS.items():
            for L in levels:
                if (var, L) in skip:
                    continue
                write(
                    buf, sample=sample, short=var, type_of_level="isobaricInhPa",
                    level=L, area=area, step=step, j_positive=j_positive,
                    values=lambda la, lo, _o=off + _LEVEL_OFFSET[L]: linear_field(la, lo, offset=_o),
                )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Lattice extraction
# ---------------------------------------------------------------------------


class TestReadLatticeFields:
    def test_ecmwf_style_multi_grid_slice(self):
        """Nordic and US grids are skipped; the Europe grid (GRIB2 342.5→39.5) is sliced."""
        data = _grib([NORDIC, US_G1, EUROPE_G2], levels=(850,))
        lat = np.array([35.0, 35.25, 59.5])
        lon = np.array([-17.5, -0.25, 0.0, 28.0])  # crosses the meridian
        out = read_lattice_fields(data, [850], lat, lon)
        assert sorted(out[850]) == ["r", "t", "u", "v"]
        for var in ("t", "r", "u", "v"):
            np.testing.assert_allclose(
                out[850][var], _expected(var, 850, lat, lon), atol=1e-3,
            )

    def test_full_frontal_grid_is_a_slice_of_europe(self):
        lat, lon = build_grid_coords()
        data = _grib([EUROPE], levels=(700,), sample="regular_ll_pl_grib1")
        out = read_lattice_fields(data, [700], lat, lon)
        assert out[700]["t"].shape == (99, 183)
        np.testing.assert_allclose(out[700]["t"], _expected("t", 700, lat, lon), atol=1e-3)

    def test_gfs_style_0_360_axis(self):
        band = dict(lat_first=38.0, lat_last=34.0, lon_first=0.0, lon_last=359.75)
        data = _grib([band], levels=(925,))
        lat = np.array([35.0, 36.0])
        lon = np.array([-17.5, -0.25, 1.0, 28.0])
        out = read_lattice_fields(data, [925], lat, lon)
        np.testing.assert_allclose(out[925]["u"], _expected("u", 925, lat, lon), atol=1e-3)

    def test_icon_style_subsample(self):
        """0.0625° grid from 29.5°N / 23.5°W holds the 0.25° lattice exactly."""
        icon = dict(lat_first=29.5, lat_last=37.0, lon_first=-23.5, lon_last=-14.0)
        data = _grib([icon], levels=(850,), step=0.0625, j_positive=True)
        lat = np.arange(35.0, 37.0 + 1e-9, 0.25)
        lon = np.arange(-17.5, -14.0 + 1e-9, 0.25)
        out = read_lattice_fields(data, [850], lat, lon)
        np.testing.assert_allclose(out[850]["v"], _expected("v", 850, lat, lon), atol=1e-3)

    def test_grid_missing_a_target_point_is_not_used(self):
        """A grid one row short of the lattice gives nothing, never a shifted field."""
        short = dict(lat_first=59.25, lat_last=35.0, lon_first=-17.5, lon_last=39.5)
        data = _grib([short], levels=(850,))
        lat, lon = build_grid_coords()
        assert read_lattice_fields(data, [850], lat, lon) == {850: {}}

    def test_missing_variable_reported_by_absence(self):
        data = _grib([EUROPE], levels=(850,), skip={("r", 850)})
        out = read_lattice_fields(data, [850], np.array([40.0]), np.array([5.0]))
        assert sorted(out[850]) == ["t", "u", "v"]
        assert level_inputs(out[850]) is None

    def test_lattice_indices_rejects_off_lattice_targets(self):
        axis = np.arange(35.0, 40.0 + 1e-9, 0.25)
        assert lattice_indices(axis, axis, np.array([35.1]), np.array([35.0])) is None


class TestUnits:
    def test_dewpoint_matches_sounding_path_magnus(self):
        from weatherbrief.fetch.open_meteo import magnus_dewpoint

        t = np.array([-20.0, 0.0, 15.0, 25.0])
        rh = np.array([30.0, 80.0, 100.0, 55.0])
        np.testing.assert_allclose(
            dewpoint_magnus_c(t, rh), [magnus_dewpoint(a, b) for a, b in zip(t, rh)],
            rtol=1e-12,
        )

    def test_level_inputs_converts_kelvin_and_ms(self):
        f = {k: np.array([[v]]) for k, v in {"t": 283.15, "r": 100.0, "u": 10.0, "v": -5.0}.items()}
        t, td, u, v = level_inputs(f)
        assert t[0, 0] == pytest.approx(10.0)
        assert td[0, 0] == pytest.approx(10.0)  # saturated
        assert u[0, 0] == pytest.approx(36.0)
        assert v[0, 0] == pytest.approx(-18.0)

    def test_forecast_step_hours_default_is_40_steps(self):
        hours = forecast_step_hours(5, 3)
        assert hours[0] == 0 and hours[-1] == 117 and len(hours) == 40


# ---------------------------------------------------------------------------
# Run resolution
# ---------------------------------------------------------------------------


class _FakeSession:
    """HEAD 200 for URLs in ``ok``, 404 otherwise."""

    def __init__(self, ok):
        self.ok = ok
        self.heads: list[str] = []

    def head(self, url, timeout=None):
        self.heads.append(url)
        return type("R", (), {"status_code": 200 if any(o in url for o in self.ok) else 404})()


NOW = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)


class TestResolve:
    def test_gfs_picks_newest_run_with_last_step(self):
        sess = _FakeSession(["gfs.20261024/00/atmos/gfs.t00z.pgrb2.0p25.f000.idx",
                             "gfs.20261024/00/atmos/gfs.t00z.pgrb2.0p25.f117.idx"])
        run = grib_source.resolve_run("gfs", [0, 117], [850], now=NOW, session=sess)
        assert run.init == datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
        assert run.source == "gfs-s3"

    def test_gfs_steps_back_when_last_step_not_published(self):
        sess = _FakeSession(["t00z.pgrb2.0p25.f000.idx",
                             "gfs.20261023/18/atmos/gfs.t18z.pgrb2.0p25.f000.idx",
                             "gfs.20261023/18/atmos/gfs.t18z.pgrb2.0p25.f117.idx"])
        run = grib_source.resolve_run("gfs", [0, 117], [850], now=NOW, session=sess)
        assert run.init == datetime(2026, 10, 23, 18, tzinfo=timezone.utc)

    def test_icon_skips_short_runs_and_checks_pressure_levels(self):
        # 03Z (short run) answers its probe but has a 30 h horizon; 00Z is main.
        sess = _FakeSession(["model-level_2026102403_000_74_P",
                             "model-level_2026102400_000_74_P",
                             # the run finder's last-hour probe (now + 31 h → f040)
                             "model-level_2026102400_040_74_P",
                             "pressure-level_2026102400_117_850_T.grib2.bz2"])
        run = grib_source.resolve_run("icon", [0, 117], [850], now=NOW, session=sess)
        assert run.init == datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
        assert not any("2026102403" in u for u in sess.heads)

    def test_icon_none_when_nothing_published(self):
        assert grib_source.resolve_run(
            "icon", [0, 117], [850], now=NOW, session=_FakeSession([]),
        ) is None

    def test_ecmwf_needs_ready_run_with_every_step(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))

        def touch(init: datetime, step: int, feed="a2"):
            valid = init + timedelta(hours=step)
            (tmp_path / (
                f"brg_{feed}_ifs-ens-cf_od_oper_fc_{init:%Y%m%dT%H%M%SZ}_"
                f"{valid:%Y%m%dT%H%M%SZ}_{step}h"
            )).write_bytes(b"")

        new = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
        old = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        for s in (0, 3):
            touch(old, s)
        touch(new, 0)  # f003 missing on the newest run
        touch(new, 3, feed="a1")  # a1 does not count
        for init in (new, old):
            (tmp_path / f".ready_{init:%Y%m%d}_{init:%H}z").write_text("files=1/1")
        run = grib_source.resolve_run("ecmwf", [0, 3], [850])
        assert run.init == old
        assert run.fetch_step(3).name.endswith("_3h")
        assert run.fetch_step(6) is None

    def test_gfs_pinned_init_skips_the_run_finder(self):
        pinned = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        sess = _FakeSession(["gfs.20261023/12/atmos/gfs.t12z.pgrb2.0p25.f117.idx"])
        run = grib_source.resolve_run("gfs", [0, 117], [850], now=NOW, session=sess,
                                      init=pinned)
        assert run.init == pinned
        assert sess.heads == [
            "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261023/12/atmos/"
            "gfs.t12z.pgrb2.0p25.f117.idx",
        ]

    def test_pinned_init_gone_is_none_not_another_run(self):
        sess = _FakeSession(["model-level_2026102400_000_74_P",
                             "pressure-level_2026102400_117_850_T"])
        assert grib_source.resolve_run(
            "icon", [0, 117], [850], now=NOW, session=sess,
            init=datetime(2026, 10, 22, 0, tzinfo=timezone.utc),
        ) is None

    def test_ecmwf_none_without_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
        assert grib_source.resolve_run("ecmwf", [0], [850]) is None

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            grib_source.resolve_run("ukmo", [0], [850])


# ---------------------------------------------------------------------------
# run_once on the GRIB source
# ---------------------------------------------------------------------------

INIT = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
# The 08Z sample hour after INIT: INIT is the cycle every model should have.
SAMPLE_NOW = INIT + timedelta(hours=8)


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(grib_source, "_STEP_RETRY_DELAY_S", 0.0)


@pytest.fixture
def flat_terrain():
    with patch(
        "weatherbrief.hewson.precompute.build_terrain_elevation",
        side_effect=lambda lat, lon: np.zeros((len(lat), len(lon))),
    ):
        yield


@pytest.fixture
def no_open_meteo():
    """Any Open-Meteo grid fetch or metadata call fails the test."""
    def boom(*a, **kw):
        raise AssertionError("Open-Meteo must not be called on the GRIB path")

    with patch("weatherbrief.hewson.precompute.fetch_grid_fields", side_effect=boom), \
         patch("weatherbrief.fetch.model_status.fetch_model_metadata", side_effect=boom), \
         patch("weatherbrief.fetch.open_meteo.OpenMeteoClient", side_effect=boom):
        yield


def _fake_run(model="gfs", fail_hours=(), calls=None):
    blob = _grib([EUROPE])

    def fetch_step(hour):
        if calls is not None:
            calls.append(hour)
        return None if hour in fail_hours else blob

    return GribRun(model, grib_source.SOURCE_LABELS[model], INIT, fetch_step)


def test_run_once_grib_writes_trimmed_snapshot(tmp_path, flat_terrain, no_open_meteo):
    with patch.object(grib_source, "resolve_run", return_value=_fake_run()):
        result = run_once(
            models=["gfs"], output_dir=tmp_path, forecast_days=1, stride_hours=6,
            retention_hours=10**6, source=SOURCE_GRIB, now=SAMPLE_NOW,
        )
    path = result.snapshots["gfs"]
    assert path == snapshot_path("gfs", int(INIT.timestamp()), tmp_path)
    assert result.api_calls_total == 0
    assert result.sources["gfs"] == "gfs-s3 init 2026-10-24T00:00Z"
    snap = load_snapshot(path)
    assert snap["theta_e_850"].shape == (4, 99, 183)
    assert snap["valid_times"].astype("datetime64[h]").astype(str).tolist() == [
        "2026-10-24T00", "2026-10-24T06", "2026-10-24T12", "2026-10-24T18",
    ]
    for L in DEFAULT_LEVELS:
        for metric in ("theta_e", "gradient", "neg_laplacian", "tfp", "advection", "tendency"):
            assert np.isfinite(snap[f"{metric}_{L}"]).all(), f"{metric}_{L}"
    # θe grows with T/Td, so the stack is in a physical range (K).
    assert 280 < float(snap["theta_e_850"].mean()) < 400
    # The inbox validator accepts what the node writes.
    from weatherbrief.hewson.inbox import validate_snapshot

    lat, lon = build_grid_coords()
    assert validate_snapshot(path, lat, lon) == int(INIT.timestamp())


def test_run_once_grib_incomplete_step_skips_model(tmp_path, flat_terrain, no_open_meteo):
    with patch.object(grib_source, "resolve_run", return_value=_fake_run(fail_hours={12})):
        result = run_once(
            models=["gfs"], output_dir=tmp_path, forecast_days=1, stride_hours=6,
            now=SAMPLE_NOW,
        )
    assert result.skipped == {"gfs": "incomplete"}
    assert not list(tmp_path.rglob("gfs/*.npz"))


def test_run_once_grib_holey_level_skips_model(tmp_path, flat_terrain, no_open_meteo):
    """A level too sparse to prepare must drop the model, not leave a NaN slice."""
    with patch.object(grib_source, "resolve_run", return_value=_fake_run()), \
         patch("weatherbrief.hewson.precompute.prepare_level_fields", return_value=None):
        result = run_once(models=["gfs"], output_dir=tmp_path, forecast_days=1,
                          stride_hours=12, now=SAMPLE_NOW)
    assert result.skipped == {"gfs": "too-many-missing"}
    assert not list(tmp_path.rglob("gfs/*.npz"))


def test_resolve_run_session_pool_fits_icon_workers():
    adapter = grib_source._new_session().get_adapter("https://opendata.dwd.de")
    assert adapter._pool_maxsize >= grib_source._ICON_DOWNLOAD_WORKERS


def test_run_once_grib_unresolved_model_skipped(tmp_path, flat_terrain, no_open_meteo):
    with patch.object(grib_source, "resolve_run", return_value=None):
        result = run_once(models=["ecmwf"], output_dir=tmp_path, forecast_days=1)
    assert result.skipped == {"ecmwf": "no-grib-run"}


def test_run_once_grib_skip_existing_reads_nothing(tmp_path, flat_terrain, no_open_meteo):
    existing = snapshot_path("icon", int(INIT.timestamp()), tmp_path)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"")
    calls: list[int] = []
    with patch.object(grib_source, "resolve_run", return_value=_fake_run("icon", calls=calls)):
        result = run_once(models=["icon"], output_dir=tmp_path, forecast_days=1,
                          now=SAMPLE_NOW)
    assert result.skipped == {"icon": "fresh"}
    assert calls == []


def test_run_once_pinned_init_skips_purge(tmp_path, flat_terrain, no_open_meteo):
    seen = {}

    def fake_resolve(model, hours, levels, now=None, init=None):
        seen["init"] = init
        return _fake_run()

    with patch.object(grib_source, "resolve_run", side_effect=fake_resolve), \
         patch("weatherbrief.hewson.precompute.purge_old_snapshots") as purge:
        result = run_once(models=["gfs"], output_dir=tmp_path, forecast_days=1,
                          stride_hours=12, init=INIT)
    assert seen["init"] == INIT
    assert result.snapshots["gfs"] is not None
    purge.assert_not_called()


@pytest.mark.parametrize("model,now,want", [
    # 08Z / 20Z sample hours expect the 00Z / 12Z run of every model.
    ("ecmwf", "2026-10-24T08:00", "2026-10-24T00:00"),
    ("ecmwf", "2026-10-24T20:00", "2026-10-24T12:00"),
    ("gfs", "2026-10-24T08:00", "2026-10-24T00:00"),
    ("icon", "2026-10-24T20:00", "2026-10-24T12:00"),
    # Before the morning ECMWF delivery, yesterday's 12Z is still current.
    ("ecmwf", "2026-10-24T07:00", "2026-10-23T12:00"),
    ("gfs", "2026-10-24T11:30", "2026-10-24T06:00"),
])
def test_expected_init(model, now, want):
    got = grib_source.expected_init(
        model, datetime.fromisoformat(now).replace(tzinfo=timezone.utc))
    assert got == datetime.fromisoformat(want).replace(tzinfo=timezone.utc)


def test_run_once_stale_ecmwf_fails_others_still_written(
        tmp_path, flat_terrain, no_open_meteo):
    """Option A: the node's 07:15 ECMWF sync did not land. The resolver finds
    only yesterday's 12Z, which is already on disk; that must be a reported
    failure, not "fresh", and GFS still publishes."""
    old = INIT - timedelta(hours=12)
    on_disk = snapshot_path("ecmwf", int(old.timestamp()), tmp_path)
    on_disk.parent.mkdir(parents=True)
    on_disk.write_bytes(b"")
    calls: list[int] = []

    def fake_resolve(model, hours, levels, now=None, init=None):
        if model == "ecmwf":
            run = _fake_run("ecmwf", calls=calls)
            run.init = old
            return run
        return _fake_run(model)

    with patch.object(grib_source, "resolve_run", side_effect=fake_resolve):
        result = run_once(models=["ecmwf", "gfs"], output_dir=tmp_path,
                          forecast_days=1, stride_hours=12, now=SAMPLE_NOW)
    assert result.skipped == {"ecmwf": "stale-init"}
    assert calls == []
    assert result.snapshots["gfs"] is not None
    assert result.sources["ecmwf"] == "ecmwf-ecpds init 2026-10-23T12:00Z"


def test_run_once_allows_stale_when_asked(tmp_path, flat_terrain, no_open_meteo):
    run = _fake_run("ecmwf")
    run.init = INIT - timedelta(hours=12)
    with patch.object(grib_source, "resolve_run", return_value=run):
        result = run_once(models=["ecmwf"], output_dir=tmp_path, forecast_days=1,
                          stride_hours=12, now=SAMPLE_NOW, require_current=False)
    assert result.skipped == {}
    assert result.snapshots["ecmwf"] is not None


def test_cli_exits_1_on_stale_model(tmp_path, capsys):
    from weatherbrief.hewson import cli
    from weatherbrief.hewson.precompute import SnapshotResult

    fake = SnapshotResult(snapshots={"gfs": tmp_path / "g.npz"},
                          skipped={"ecmwf": "stale-init", "icon": "fresh"})
    with patch.object(cli, "run_once", return_value=fake) as ro:
        rc = cli.main(["precompute", "--output-dir", str(tmp_path)])
    assert rc == 1
    assert ro.call_args.kwargs["require_current"] is True
    assert "FAILED: ecmwf (stale-init)" in capsys.readouterr().err


def test_run_once_pinned_init_rejects_open_meteo(tmp_path):
    with pytest.raises(ValueError):
        run_once(models=["gfs"], output_dir=tmp_path, source="open-meteo", init=INIT)


@pytest.mark.parametrize("text", ["2026-10-24T00Z", "2026-10-24T00:00Z",
                                  "2026-10-24T00:00:00Z"])
def test_cli_parse_init(text):
    from weatherbrief.hewson.cli import _parse_init

    assert _parse_init(text) == INIT


def test_ensure_terrain_cache_rebuilds_on_grid_change(tmp_path, flat_terrain):
    from weatherbrief.hewson.precompute import ensure_terrain_cache

    old_lat = np.arange(35.0, 60.0 + 1e-9, 0.25)
    old_lon = np.arange(-20.0, 28.0 + 1e-9, 0.25)
    np.savez_compressed(
        tmp_path / "terrain_mask.npz", mask=np.ones((101, 193), bool),
        lat=old_lat, lon=old_lon, elevation=np.zeros((101, 193)),
    )
    mask = ensure_terrain_cache(tmp_path)
    assert mask.shape == (99, 183)
    with np.load(tmp_path / "terrain_mask.npz") as npz:
        assert npz["elevation"].shape == (99, 183)
