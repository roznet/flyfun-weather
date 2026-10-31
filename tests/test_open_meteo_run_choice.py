"""Open-Meteo no longer decides which GRIB run is used, or whether (#957).

Its ``meta.json`` is asked only for a slot Open-Meteo actually serves: the
map's and alternates' fallback when GRIB has no run, a briefing slot fetched
from Open-Meteo (up front or as a GRIB-primary fallback), the freshness
markers of the ``*:openmeteo`` sources, frontal grids and Hewson's
Open-Meteo source. The guard below keeps that list short; the rest checks that
with ``meta.json`` returning nothing, every GRIB-built path still works.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.models import ModelSource
from weatherbrief.tasks.airport_watchlist import WatchlistAirport

UTC = timezone.utc
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
META = "weatherbrief.fetch.model_status.fetch_model_metadata"
SV = "weatherbrief.tasks.standalone_verification"

# Modules allowed to call Open-Meteo's meta.json, each with why. Keep it short:
# a new entry means Open-Meteo decides something again.
ALLOWED = {
    "weatherbrief/fetch/model_status.py":
        "defines fetch_model_metadata",
    "weatherbrief/fetch/freshness/sources.py":
        "the *:openmeteo freshness markers watch Open-Meteo itself",
    "weatherbrief/tasks/fetch.py":
        "records Open-Meteo's init for the briefing slots it served",
    "weatherbrief/tasks/standalone_verification.py":
        "map/alternates fallback when GRIB has no run, or a model off GRIB snapshots",
    "weatherbrief/frontal/cli.py":
        "frontal grids are fetched from Open-Meteo",
    "weatherbrief/frontal/grid.py":
        "frontal grids are fetched from Open-Meteo",
    "weatherbrief/hewson/precompute.py":
        "Hewson's Open-Meteo source (fallback to its GRIB source)",
}


def _uses_meta(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
            a.name == "fetch_model_metadata" for a in node.names
        ):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "fetch_model_metadata":
            return True
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_model_metadata":
            return True
    return False


class TestOnlyAllowListedModulesAskOpenMeteo:
    def test_no_module_outside_the_list_uses_meta_json(self):
        """Every use of ``fetch_model_metadata`` is on the list above.

        AST-based, so comments and docstrings naming it don't count. Before
        #957 the map cycle, alternates and every briefing asked it first and
        dropped or delayed GRIB data when it lagged or was down.
        """
        users = set()
        for path in SRC_ROOT.rglob("*.py"):
            if _uses_meta(ast.parse(path.read_text(), filename=str(path))):
                users.add(path.relative_to(SRC_ROOT).as_posix())
        offenders = sorted(users - set(ALLOWED))
        assert not offenders, (
            "Only Open-Meteo's own slots may ask its meta.json (#957); a GRIB "
            "path picks its run from its GRIB source. New users:\n  "
            + "\n  ".join(offenders)
        )
        # A stale entry would let a later regression in that module through.
        assert users == set(ALLOWED), sorted(set(ALLOWED) - users)


# ---------------------------------------------------------------------------
# ECMWF run choice for the map and alternates
# ---------------------------------------------------------------------------


def _write_run(grib_dir: Path, base: datetime, max_step: int) -> None:
    """A ready ECMWF run on disk: a1/a2 files for steps 0, 6, max_step."""
    bt = base.strftime("%Y%m%dT%H%M%SZ")
    for step in sorted({0, 6, max_step}):
        vt = (base + timedelta(hours=step)).strftime("%Y%m%dT%H%M%SZ")
        for part in ("a1", "a2"):
            (grib_dir / f"brg_{part}_ifs-ens-cf_od_oper_fc_{bt}_{vt}_{step}h").write_bytes(b"")
    (grib_dir / f".ready_{base:%Y%m%d}_{base:%H}z").write_text("")


class TestSelectEcmwfGribRun:
    DAY = datetime(2026, 10, 30, tzinfo=UTC)

    def test_newest_run_reaching_the_map_horizon_wins(self, tmp_path, monkeypatch):
        """A newer 06z run (144 h) that stops short of the 6-day map loses to
        the 00z run (168 h) that reaches it: the briefings' rule."""
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
        _write_run(tmp_path, self.DAY, 168)
        _write_run(tmp_path, self.DAY.replace(hour=6), 144)
        # Precondition: the 06z run really is short of the map's last hour.
        last = sv._planned_valid_times(self.DAY.replace(hour=6), 6, None)[-1]
        assert last > self.DAY.replace(hour=6) + timedelta(hours=144)

        with patch(META) as meta:
            files = sv._select_ecmwf_grib_run(6)
        assert {f.base_time for f in files} == {self.DAY}
        meta.assert_not_called()

    def test_newest_covering_run_wins_whatever_open_meteo_shows(self, tmp_path, monkeypatch):
        """Before #957 a ready run older than Open-Meteo's init was refused."""
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
        _write_run(tmp_path, self.DAY - timedelta(hours=12), 168)
        _write_run(tmp_path, self.DAY, 168)

        files = sv._select_ecmwf_grib_run(6)
        assert {f.base_time for f in files} == {self.DAY}

    def test_short_run_alone_is_still_used(self, tmp_path, monkeypatch):
        """No run reaches the horizon: the one reaching furthest, not Open-Meteo."""
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
        _write_run(tmp_path, self.DAY.replace(hour=6), 144)

        files = sv._select_ecmwf_grib_run(6)
        assert files and {f.base_time.hour for f in files} == {6}

    def test_none_without_a_ready_run(self, tmp_path, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setenv("ECMWF_GRIB_DIR", str(tmp_path))
        assert sv._select_ecmwf_grib_run(6) is None


class TestResolveSnapshotRun:
    GRIB_INIT = datetime(2026, 10, 30, 12, tzinfo=UTC)
    OM_INIT = datetime(2026, 10, 30, 6, tzinfo=UTC)

    def _om(self):
        return {m: SimpleNamespace(last_init_time=int(self.OM_INIT.timestamp()))
                for m in ("gfs", "icon", "ecmwf")}

    def test_grib_runs_need_no_open_meteo(self, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
        run = [SimpleNamespace(base_time=self.GRIB_INIT.replace(tzinfo=None))]
        with patch(META, return_value={}) as meta, \
             patch.object(sv, "_select_ecmwf_grib_run", return_value=run), \
             patch.object(sv, "select_grib_snapshot_init", return_value=self.GRIB_INIT):
            assert sv.resolve_snapshot_run("ecmwf", 6) == (self.GRIB_INIT, run)
            assert sv.resolve_snapshot_run("gfs", 6) == (self.GRIB_INIT, None)
            assert sv.resolve_snapshot_run("icon", 4) == (self.GRIB_INIT, None)
        meta.assert_not_called()

    def test_open_meteo_only_as_the_fallback(self, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
        with patch(META, return_value=self._om()) as meta, \
             patch.object(sv, "_select_ecmwf_grib_run", return_value=None), \
             patch.object(sv, "select_grib_snapshot_init", return_value=None):
            assert sv.resolve_snapshot_run("ecmwf", 6) == (self.OM_INIT, None)
            assert sv.resolve_snapshot_run("gfs", 6) == (self.OM_INIT, None)
        # One model per call: only the model falling back is asked.
        assert [c.args[0] for c in meta.call_args_list] == [["ecmwf"], ["gfs"]]

    def test_nothing_anywhere_skips_the_model(self, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
        with patch(META, return_value={}), \
             patch.object(sv, "select_grib_snapshot_init", return_value=None):
            assert sv.resolve_snapshot_run("gfs", 6) == (None, None)

    def test_kill_switch_keeps_open_meteo(self, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        monkeypatch.setenv("WB_GRIB_SNAPSHOTS", "0")
        with patch(META, return_value=self._om()), \
             patch.object(sv, "select_grib_snapshot_init") as select:
            assert sv.resolve_snapshot_run("icon", 4) == (self.OM_INIT, None)
        select.assert_not_called()


# ---------------------------------------------------------------------------
# meta.json returning nothing: map cycle, alternates, briefing
# ---------------------------------------------------------------------------

GRIB_INIT = datetime(2026, 10, 30, 12, tzinfo=UTC)
AIRPORTS = [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)]


def test_map_cycle_builds_every_model_from_grib_with_meta_json_down(monkeypatch):
    from weatherbrief.tasks.standalone_verification import run_standalone_cycle

    monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
    run = [SimpleNamespace(base_time=GRIB_INIT)]
    stored: list[str] = []

    def fake_store(snapshots, db, region="eu"):
        stored.extend(s["model"] for s in snapshots)
        return len(snapshots)

    def fake_model_snapshots(model, init_time, airports, session, **kw):
        assert init_time == GRIB_INIT
        return [{"model": model}], 0

    with patch("flyfun_common.db.SessionLocal") as session_local, \
         patch(META, return_value={}) as meta, \
         patch(f"{SV}._select_ecmwf_grib_run", return_value=run), \
         patch(f"{SV}.select_grib_snapshot_init", return_value=GRIB_INIT), \
         patch(f"{SV}.fetch_ecmwf_grib_snapshots", return_value=[{"model": "ecmwf"}]), \
         patch(f"{SV}.fetch_model_snapshots", side_effect=fake_model_snapshots), \
         patch(f"{SV}._store_snapshots", side_effect=fake_store), \
         patch(f"{SV}._prune_old_snapshots", return_value=0):
        db = MagicMock()
        session_local.return_value = db
        db.execute.return_value.scalar_one_or_none.return_value = None
        result = run_standalone_cycle(AIRPORTS, "/fake/db", score_observations=False)

    assert result["models_fetched"] == 3
    assert sorted(stored) == ["ecmwf", "gfs", "icon"]
    meta.assert_not_called()


def test_alternates_return_every_model_with_meta_json_down(monkeypatch):
    from weatherbrief.tasks import alternates as alt_mod

    monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
    eta = GRIB_INIT + timedelta(hours=6)
    run = [SimpleNamespace(base_time=GRIB_INIT)]

    def fake_model_snapshots(model, init_time, airports, session, *a, **kw):
        assert init_time == GRIB_INIT
        return [{"icao": "LFPG", "model": model, "forecast_hour": eta}], 0

    with patch(META, return_value={}) as meta, \
         patch(f"{SV}._select_ecmwf_grib_run", return_value=run), \
         patch(f"{SV}.select_grib_snapshot_init", return_value=GRIB_INIT), \
         patch(f"{SV}.fetch_ecmwf_grib_snapshots",
               return_value=[{"icao": "LFPG", "model": "ecmwf", "forecast_hour": eta}]), \
         patch(f"{SV}.fetch_model_snapshots", side_effect=fake_model_snapshots):
        by_icao = alt_mod._fetch_eta_snapshots(AIRPORTS, eta)

    assert set(by_icao["LFPG"]) == {"gfs", "icon", "ecmwf"}
    meta.assert_not_called()


class TestBriefingRecordsOnlyOpenMeteoSlots:
    """A briefing asks meta.json only for the slots Open-Meteo served."""

    def _run(self, monkeypatch, *, gate, enrich, meta_return):
        from tests.test_grib_primary import _run_fetch

        calls: list[list[str]] = []

        def fake_meta(models=None, timeout=5):
            calls.append(list(models))
            return {m: meta_return[m] for m in models if m in meta_return}

        with patch(META, side_effect=fake_meta):
            result, om_calls = _run_fetch(monkeypatch, enrich=enrich, gate=gate)
        return result, om_calls, calls

    @staticmethod
    def _om(*models):
        return {m: SimpleNamespace(last_init_time=1_000 + i) for i, m in enumerate(models)}

    def test_grib_only_briefing_never_asks_and_records_grib_inits(self, monkeypatch):
        from tests.test_grib_primary import _fill_window, required_hours, DEP

        from weatherbrief.api.packs import _build_pack_meta, _no_open_meteo_part
        from weatherbrief.models.storage import Flight
        from weatherbrief.pipeline import BriefingResult

        def enrich(cross_sections):
            for cs in cross_sections:
                for wf in cs.point_forecasts:
                    _fill_window(wf, required_hours(DEP, 2.0))
            return {"gfs": 111, "ecmwf": 222}, {}, {}

        result, om_calls, meta_calls = self._run(
            monkeypatch, gate=lambda m: None, enrich=enrich, meta_return={},
        )
        assert om_calls == [] and meta_calls == []
        assert sorted(result.models_fetched) == ["ecmwf", "gfs"]
        assert result.open_meteo_init_times == {}

        briefing = BriefingResult(snapshot=None, snapshot_path=Path("/tmp/none"))
        briefing.models_fetched = result.models_fetched
        briefing.grib_init_times = result.grib_init_times
        briefing.open_meteo_init_times = result.open_meteo_init_times
        meta = _build_pack_meta(
            "f", Flight(
                id="f", route_name="t", waypoints=["EGTK", "LFPG"],
                departure_time=DEP, created_at=DEP - timedelta(days=1),
            ),
            DEP - timedelta(hours=6), Path("/tmp/pack"), briefing,
            as_of_time=DEP - timedelta(hours=6),
        )
        assert meta.model_init_times == {"gfs": 111, "ecmwf": 222}
        assert meta.model_sources == {"gfs": "gfs:noaa", "ecmwf": "ecmwf:direct"}
        # No Open-Meteo "base" row in the freshness popover for either slot.
        assert _no_open_meteo_part(meta, "gfs") and _no_open_meteo_part(meta, "ecmwf")

    def test_open_meteo_first_slot_is_the_only_one_asked(self, monkeypatch):
        from tests.test_grib_primary import _cover_all

        result, om_calls, meta_calls = self._run(
            monkeypatch, gate=None, enrich=_cover_all(ModelSource.ECMWF),
            meta_return=self._om("gfs", "ecmwf"),
        )
        assert [m for m, _ in om_calls] == ["gfs"]
        assert meta_calls == [["gfs"]]
        assert result.open_meteo_init_times == {"gfs": 1_000}

    def test_grib_primary_fallback_is_asked_after_it_fell_back(self, monkeypatch):
        from tests.test_grib_primary import _cover_all

        result, _, meta_calls = self._run(
            monkeypatch, gate=None,
            enrich=_cover_all(ModelSource.ECMWF, skip={1, 2}),
            meta_return=self._om("gfs", "ecmwf"),
        )
        assert result.grib_primary == {"ecmwf": 2}
        assert meta_calls == [["gfs"], ["ecmwf"]]
        assert result.open_meteo_init_times == {"gfs": 1_000, "ecmwf": 1_001}

    def test_meta_json_down_still_completes(self, monkeypatch):
        from tests.test_grib_primary import _cover_all

        result, _, _ = self._run(
            monkeypatch, gate=None, enrich=_cover_all(ModelSource.ECMWF),
            meta_return={},
        )
        assert sorted(result.models_fetched) == ["ecmwf", "gfs"]
        assert result.open_meteo_init_times == {}


class TestBriefingHorizonSkip:
    """Open-Meteo's data_end no longer drops a slot GRIB will build."""

    DEP = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)  # 2 h flight, ends 11:00Z

    def _run(self, gate):
        from tests.test_fetch_parallel import _disable_elevation, _make_forecast, _route

        from weatherbrief.fetch.grib import primary
        from weatherbrief.tasks import fetch as fetch_module

        def fake_fetch_multi_point(self, points, model, *, start_date=None, end_date=None, chunk_size=None):
            self._record_call()
            return [_make_forecast(p, model) for p in points]

        short = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)  # short of 11:00Z
        with _disable_elevation(), patch(
            "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
            new=fake_fetch_multi_point,
        ), patch.object(
            fetch_module, "_open_meteo_live_horizons", return_value={"ecmwf": short},
        ), patch(
            "weatherbrief.fetch.grib.enrich_forecasts", return_value=({}, {}, {}),
        ), patch.object(primary, "grib_primary_skip_reason", new=gate), \
             patch(META, return_value={}):
            return fetch_module.run_fetch(
                route=_route(), departure_time=self.DEP,
                models=[ModelSource.GFS, ModelSource.ECMWF],
                enrich_grib=True,
            )

    def test_grib_primary_slot_ignores_open_meteo_data_end(self):
        result = self._run(lambda m, *a, **kw: None if m == ModelSource.ECMWF else "disabled")
        assert "ecmwf" in result.models_fetched

    def test_open_meteo_slot_is_still_skipped(self):
        result = self._run(lambda m, *a, **kw: "no_run")
        assert result.models_fetched == ["gfs"]


# ---------------------------------------------------------------------------
# Map cycle freshness wait
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env,expected", [
    (None, [("gfs:noaa", "gfs"), ("icon_eu:dwd", "icon_eu"), ("ecmwf:direct", "ecmwf")]),
    ("0", [("gfs:openmeteo", "gfs"), ("icon:openmeteo", "icon"), ("ecmwf:direct", "ecmwf")]),
    ("icon", [("gfs:openmeteo", "gfs"), ("icon_eu:dwd", "icon_eu"), ("ecmwf:direct", "ecmwf")]),
])
def test_map_wait_watches_the_source_the_run_comes_from(monkeypatch, env, expected):
    from weatherbrief.fetch.freshness.registry import SOURCE_REGISTRY
    from weatherbrief.scheduler import _forecast_fetch_freshness_sources

    if env is None:
        monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
    else:
        monkeypatch.setenv("WB_GRIB_SNAPSHOTS", env)
    got = _forecast_fetch_freshness_sources()
    assert got == expected
    assert all(source in SOURCE_REGISTRY for source, _ in got)
