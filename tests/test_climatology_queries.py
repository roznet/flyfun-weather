"""Tests for the climatology helper + view #1 endpoint (issue #155)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from weatherbrief.api.app import create_app  # noqa: F401 (avoid circular import)
from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import (
    AirportDailySummaryRow,
    AirportMonthlySummaryRow,
)
from weatherbrief.tasks.climatology_queries import (
    MonthWindow,
    get_category_climatology,
    get_phenomena_climatology,
    get_volatility_climatology,
    get_volatility_leaderboard,
    get_wind_climatology,
    resolve_month_window,
)


FAKE_COORDS = {
    "EGKK": (51.15, -0.19),
    "LFPG": (49.01, 2.55),
    "EDDF": (50.03, 8.57),
}


@pytest.fixture
def db_engine():
    """Per-test engine — these tests call db_session.commit()."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# resolve_month_window
# ---------------------------------------------------------------------------


class TestResolveMonthWindow:
    def test_completed_month(self):
        now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        w = resolve_month_window("2026-04", now_utc=now)
        assert w == MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)

    def test_current_month_is_mtd(self):
        now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        w = resolve_month_window("2026-05", now_utc=now)
        assert w.is_mtd is True
        assert w.month == date(2026, 5, 1)
        assert w.as_of_date == date(2026, 5, 18)  # yesterday-UTC

    def test_first_of_month_has_no_completed_days(self):
        now = datetime(2026, 5, 1, 6, 0, tzinfo=timezone.utc)
        w = resolve_month_window("2026-05", now_utc=now)
        assert w.is_mtd is True
        assert w.as_of_date is None

    def test_future_month_rejected(self):
        now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="future"):
            resolve_month_window("2026-06", now_utc=now)

    def test_malformed_rejected(self):
        with pytest.raises(ValueError, match="YYYY-MM"):
            resolve_month_window("not-a-month")


# ---------------------------------------------------------------------------
# get_category_climatology
# ---------------------------------------------------------------------------


def _seed_monthly(db, *, icao, month, **counts):
    defaults = dict(
        n_obs=0, n_vfr=0, n_mvfr=0, n_ifr=0, n_lifr=0,
        n_ts=0, n_fg=0, n_wind_over_25kt=0, category_changes=0,
    )
    defaults.update(counts)
    db.add(AirportMonthlySummaryRow(
        icao=icao,
        month=datetime(month.year, month.month, 1, tzinfo=timezone.utc),
        **defaults,
    ))


def _seed_daily(db, *, icao, day, **counts):
    defaults = dict(
        n_obs=0, n_vfr=0, n_mvfr=0, n_ifr=0, n_lifr=0,
        n_ts=0, n_fg=0, n_category_changes=0,
    )
    defaults.update(counts)
    db.add(AirportDailySummaryRow(icao=icao, date=day, **defaults))


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestGetCategoryClimatology:
    def test_completed_month_reads_monthly_summary(self, _mock_coords, db_session):
        # April 2026 — fully completed
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=100, n_vfr=80, n_mvfr=10, n_ifr=7, n_lifr=3)
        _seed_monthly(db_session, icao="LFPG", month=date(2026, 4, 1),
                      n_obs=200, n_vfr=100, n_mvfr=50, n_ifr=40, n_lifr=10)
        db_session.commit()

        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_category_climatology(db_session, window, "/fake/airports.db")

        assert result["month"] == "2026-04"
        assert result["is_mtd"] is False
        assert "as_of_date" not in result
        airports = {a["icao"]: a for a in result["airports"]}
        assert airports["EGKK"]["n_obs"] == 100
        assert airports["EGKK"]["pct_vfr"] == 80.0
        assert airports["LFPG"]["pct_ifr"] == 20.0

    def test_mtd_sums_daily_summary(self, _mock_coords, db_session):
        # May 2026 — MTD through May 3
        for day, n_vfr in [(date(2026, 5, 1), 20), (date(2026, 5, 2), 15),
                           (date(2026, 5, 3), 10)]:
            _seed_daily(db_session, icao="EGKK", day=day,
                        n_obs=24, n_vfr=n_vfr,
                        n_mvfr=24 - n_vfr - 2, n_ifr=1, n_lifr=1)
        db_session.commit()

        window = MonthWindow(
            month=date(2026, 5, 1), is_mtd=True, as_of_date=date(2026, 5, 3),
        )
        result = get_category_climatology(db_session, window, "/fake/airports.db")

        assert result["is_mtd"] is True
        assert result["as_of_date"] == "2026-05-03"
        airports = {a["icao"]: a for a in result["airports"]}
        # SUM check: 3 days × 24 obs = 72, vfr = 20+15+10 = 45
        assert airports["EGKK"]["n_obs"] == 72
        assert airports["EGKK"]["n_vfr"] == 45
        assert airports["EGKK"]["pct_vfr"] == round(100.0 * 45 / 72, 1)

    def test_mtd_excludes_dates_outside_window(self, _mock_coords, db_session):
        # April rows should not appear in May MTD result
        _seed_daily(db_session, icao="EGKK", day=date(2026, 4, 30),
                    n_obs=24, n_vfr=24)
        _seed_daily(db_session, icao="EGKK", day=date(2026, 5, 1),
                    n_obs=24, n_vfr=10, n_mvfr=14)
        db_session.commit()

        window = MonthWindow(
            month=date(2026, 5, 1), is_mtd=True, as_of_date=date(2026, 5, 1),
        )
        result = get_category_climatology(db_session, window, "/fake/airports.db")
        airports = {a["icao"]: a for a in result["airports"]}
        assert airports["EGKK"]["n_obs"] == 24  # not 48
        assert airports["EGKK"]["n_vfr"] == 10

    def test_skips_airports_without_coords(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="ZZZZ", month=date(2026, 4, 1),
                      n_obs=10, n_vfr=10)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_category_climatology(db_session, window, "/fake/airports.db")
        assert all(a["icao"] != "ZZZZ" for a in result["airports"])

    def test_empty_mtd_when_no_completed_days(self, _mock_coords, db_session):
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True, as_of_date=None)
        result = get_category_climatology(db_session, window, "/fake/airports.db")
        # All watchlist airports still plot (with zero counts); none has data.
        assert len(result["airports"]) == len(FAKE_COORDS)
        assert all(a["n_obs"] == 0 for a in result["airports"])
        assert all("pct_vfr" not in a for a in result["airports"])


# ---------------------------------------------------------------------------
# Endpoint test (mirrors test_map_queries _make_client pattern)
# ---------------------------------------------------------------------------


TEST_SECRET = "test-jwt-secret"


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


def _make_client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_EMAILS", "admin@test.com")
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("DISABLE_RETENTION", "1")
    monkeypatch.setenv("DISABLE_VERIFICATION", "1")
    monkeypatch.setenv("DISABLE_DIGEST", "1")
    monkeypatch.setenv("DISABLE_STANDALONE_VERIFICATION", "1")

    app = create_app()

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestCategoryEndpoint:
    def test_returns_completed_month(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _seed_monthly(session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=100, n_vfr=80, n_mvfr=10, n_ifr=7, n_lifr=3)
        session.commit()
        session.close()

        resp = client.get("/api/climatology/category?month=2026-04")
        assert resp.status_code == 200
        data = resp.json()
        assert data["month"] == "2026-04"
        assert data["is_mtd"] is False
        # All watchlist airports plot; only EGKK has data this test.
        assert len(data["airports"]) == len(FAKE_COORDS)
        by_icao = {a["icao"]: a for a in data["airports"]}
        assert by_icao["EGKK"]["pct_vfr"] == 80.0
        assert "pct_vfr" not in by_icao["LFPG"]   # no data → no pct fields

    def test_rejects_bad_month(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        resp = client.get("/api/climatology/category?month=bogus")
        assert resp.status_code == 400

    def test_rejects_future_month(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        resp = client.get("/api/climatology/category?month=2099-12")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# View #2 — Phenomena (TS / fog)
# ---------------------------------------------------------------------------


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestPhenomenaClimatology:
    def test_ts_percentage(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=1000, n_ts=23, n_fg=4)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_phenomena_climatology(db_session, window, "/fake/airports.db", "ts")
        assert result["dataset"] == "phenomena"
        assert result["metric"] == "ts"
        assert result["unit"] == "%"
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 2.3
        assert by_icao["EGKK"]["n_ts"] == 23

    def test_fog_percentage(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, n_fg=18)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_phenomena_climatology(db_session, window, "/fake/airports.db", "fog")
        assert result["metric"] == "fog"
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 2.5

    def test_invalid_kind_400(self, _mock_coords, db_session):
        from fastapi import HTTPException
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        with pytest.raises(HTTPException) as exc:
            get_phenomena_climatology(db_session, window, "/fake/airports.db", "snow")
        assert exc.value.status_code == 400

    def test_mtd_sums_ts_from_daily(self, _mock_coords, db_session):
        for d, n_ts in [(date(2026, 5, 1), 2), (date(2026, 5, 2), 5)]:
            _seed_daily(db_session, icao="EGKK", day=d, n_obs=24, n_ts=n_ts)
        db_session.commit()
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True,
                             as_of_date=date(2026, 5, 2))
        result = get_phenomena_climatology(db_session, window, "/fake/airports.db", "ts")
        by_icao = {a["icao"]: a for a in result["airports"]}
        # 7 ts events / 48 obs * 100 ≈ 14.58
        assert by_icao["EGKK"]["n_ts"] == 7
        assert by_icao["EGKK"]["value"] == round(100.0 * 7 / 48, 2)


# ---------------------------------------------------------------------------
# View #3 — Wind
# ---------------------------------------------------------------------------


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestWindClimatology:
    def test_over25_as_percentage(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, n_wind_over_25kt=72)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_wind_climatology(db_session, window, "/fake/airports.db", "over25")
        assert result["unit"] == "%"
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 10.0

    def test_p95_returns_kt(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, wind_p95_kt=28.5)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_wind_climatology(db_session, window, "/fake/airports.db", "p95")
        assert result["unit"] == "kt"
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 28.5

    def test_gust_max_completed(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, gust_max_kt=47)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_wind_climatology(db_session, window, "/fake/airports.db", "gust")
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 47.0

    def test_gust_max_mtd_uses_max_not_sum(self, _mock_coords, db_session):
        # MAX semantics: peak gust across days, not the sum.
        for d, gust in [(date(2026, 5, 1), 30), (date(2026, 5, 2), 55),
                        (date(2026, 5, 3), 22)]:
            _seed_daily(db_session, icao="EGKK", day=d, n_obs=24, gust_max_kt=gust)
        db_session.commit()
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True,
                             as_of_date=date(2026, 5, 3))
        result = get_wind_climatology(db_session, window, "/fake/airports.db", "gust")
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["value"] == 55.0

    def test_mtd_rejects_over25(self, _mock_coords, db_session):
        from fastapi import HTTPException
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True,
                             as_of_date=date(2026, 5, 1))
        with pytest.raises(HTTPException) as exc:
            get_wind_climatology(db_session, window, "/fake/airports.db", "over25")
        assert exc.value.status_code == 400
        assert "month-to-date" in str(exc.value.detail).lower()

    def test_mtd_rejects_p95(self, _mock_coords, db_session):
        from fastapi import HTTPException
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True,
                             as_of_date=date(2026, 5, 1))
        with pytest.raises(HTTPException) as exc:
            get_wind_climatology(db_session, window, "/fake/airports.db", "p95")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# View #4 — Volatility
# ---------------------------------------------------------------------------


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestVolatility:
    def test_completed_month_uses_category_changes_column(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, category_changes=48)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_volatility_climatology(db_session, window, "/fake/airports.db")
        by_icao = {a["icao"]: a for a in result["airports"]}
        # 48 / 720 = 0.067
        assert by_icao["EGKK"]["value"] == round(48 / 720, 3)
        assert by_icao["EGKK"]["n_changes"] == 48

    def test_mtd_sums_n_category_changes(self, _mock_coords, db_session):
        for d, chg in [(date(2026, 5, 1), 3), (date(2026, 5, 2), 5)]:
            _seed_daily(db_session, icao="EGKK", day=d,
                        n_obs=24, n_category_changes=chg)
        db_session.commit()
        window = MonthWindow(month=date(2026, 5, 1), is_mtd=True,
                             as_of_date=date(2026, 5, 2))
        result = get_volatility_climatology(db_session, window, "/fake/airports.db")
        by_icao = {a["icao"]: a for a in result["airports"]}
        assert by_icao["EGKK"]["n_changes"] == 8
        assert by_icao["EGKK"]["value"] == round(8 / 48, 3)

    def test_leaderboard_min_obs_filter(self, _mock_coords, db_session):
        # 50 obs is below the completed-month min (100) — should be filtered out.
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=50, category_changes=10)
        _seed_monthly(db_session, icao="LFPG", month=date(2026, 4, 1),
                      n_obs=200, category_changes=20)
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_volatility_leaderboard(db_session, window, "/fake/airports.db", limit=10)
        icaos = [r["icao"] for r in result["rows"]]
        assert "EGKK" not in icaos
        assert "LFPG" in icaos
        assert result["min_n_obs"] == 100

    def test_leaderboard_ranks_by_value_desc(self, _mock_coords, db_session):
        _seed_monthly(db_session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=200, category_changes=40)  # 0.2
        _seed_monthly(db_session, icao="LFPG", month=date(2026, 4, 1),
                      n_obs=200, category_changes=10)  # 0.05
        _seed_monthly(db_session, icao="EDDF", month=date(2026, 4, 1),
                      n_obs=200, category_changes=60)  # 0.3
        db_session.commit()
        window = MonthWindow(month=date(2026, 4, 1), is_mtd=False, as_of_date=None)
        result = get_volatility_leaderboard(db_session, window, "/fake/airports.db", limit=10)
        icaos = [r["icao"] for r in result["rows"]]
        assert icaos == ["EDDF", "EGKK", "LFPG"]


# ---------------------------------------------------------------------------
# Endpoint smoke tests for the new routes
# ---------------------------------------------------------------------------


@patch(
    "weatherbrief.tasks.climatology_queries._get_coords",
    return_value=FAKE_COORDS,
)
class TestNewEndpoints:
    def test_phenomena_endpoint(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _seed_monthly(session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, n_ts=18, n_fg=9)
        session.commit()
        session.close()

        resp = client.get("/api/climatology/phenomena?month=2026-04&kind=ts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dataset"] == "phenomena"
        assert data["metric"] == "ts"

    def test_wind_endpoint_rejects_mtd_over25(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")
        resp = client.get(f"/api/climatology/wind?month={current_month}&metric=over25")
        assert resp.status_code == 400

    def test_volatility_endpoint(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _seed_monthly(session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=720, category_changes=48)
        session.commit()
        session.close()

        resp = client.get("/api/climatology/volatility?month=2026-04")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dataset"] == "volatility"

    def test_volatility_leaderboard_endpoint(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _seed_monthly(session, icao="EGKK", month=date(2026, 4, 1),
                      n_obs=200, category_changes=40)
        session.commit()
        session.close()

        resp = client.get("/api/climatology/volatility/top?month=2026-04&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert any(r["icao"] == "EGKK" for r in data["rows"])
