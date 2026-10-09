"""Tests for /api/observed — status, overlay imagery, lightning points (#574).

Imagery is served here rather than embedded in ``briefing.json``: a corridor
of 2 km composite is hundreds of kilobytes, and every pack load would pay for
a layer most of them never draw.  These tests pin that separation, the
enable-gate, and the fact that an overlay carries its own age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.api.deps import current_user_id_short
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    FrameStore,
)

FIXTURES = Path(__file__).parent / "observed" / "data"


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


@pytest.fixture
def observed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-observed")
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    for flag in (
        "DISABLE_SCHEDULER", "DISABLE_RETENTION", "DISABLE_VERIFICATION",
        "DISABLE_DIGEST", "DISABLE_STANDALONE_VERIFICATION",
        "DISABLE_ECMWF_WATCHER", "DISABLE_HEWSON_PRECOMPUTE",
        "DISABLE_METAR_INGEST", "DISABLE_FORECAST_FETCH",
        "DISABLE_FRESHNESS_LOOP", "DISABLE_ANALYTICS_ROLLUP",
    ):
        monkeypatch.setenv(flag, "1")
    return tmp_path / "data"


@pytest.fixture
def stocked(observed_env):
    """A frame store under DATA_DIR holding one recent frame per source.

    Sidecars are built the way the collector builds them — by reading the
    frame that was just written — so the attribution under test is the one
    the payload actually carries, not a literal invented here.
    """
    from weatherbrief.observed import ctth, lightning, opera

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = FrameStore(observed_env / "observed")
    for source, filename, describe in (
        (SOURCE_OPERA_DBZH, "opera_dbzh.h5", lambda p: opera.read_metadata(p, "DBZH")),
        (SOURCE_EUMETSAT_CTTH, "ctth.nc", ctth.read_metadata),
        (SOURCE_EUMETSAT_LI, "li_flashes.nc", lightning.read_metadata),
    ):
        path = store.write_payload(source, now, (FIXTURES / filename).read_bytes())
        store.write_sidecar(source, now, describe(path))
    return store


def _build_app(app_db):
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
    return app


@pytest.fixture
def client(app_db, observed_env):
    app = _build_app(app_db)
    app.dependency_overrides[current_user_id_short] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_anon(app_db, observed_env):
    return TestClient(_build_app(app_db), raise_server_exceptions=False)


BBOX = {"south": 49.8, "west": 0.4, "north": 51.2, "east": 2.9}


# --- Gating ----------------------------------------------------------------


def test_endpoints_require_authentication(client_anon):
    assert client_anon.get("/api/observed/status").status_code in (401, 403)


def test_endpoints_are_absent_unless_the_collector_is_enabled(
    app_db, observed_env, monkeypatch
):
    """A deployment without the collector must not advertise the feature."""
    # Set falsy rather than delete: create_app() calls load_dotenv(), which
    # would repopulate a deleted var from a dev .env but never overrides one
    # that is already set.
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "0")
    app = _build_app(app_db)
    app.dependency_overrides[current_user_id_short] = lambda: DEV_USER_ID
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/observed/status").status_code == 404


# --- Status ----------------------------------------------------------------


def test_status_lists_every_source_with_its_own_age(client, stocked):
    payload = client.get("/api/observed/status").json()
    by_source = {s["source"]: s for s in payload["sources"]}
    assert set(by_source) == {
        "opera_dbzh", "opera_rate", "eumetsat_li", "eumetsat_ctth",
        # Pseudo-source: the client cannot draw a legend for a layer it can
        # select unless status lists it alongside the collected streams.
        "eumetsat_ctth_temp",
    }
    assert by_source["opera_dbzh"]["available"] is True
    assert by_source["opera_dbzh"]["age_minutes"] < 5
    # No payload-level "as of": the four streams do not share an instant.
    assert "as_of" not in payload
    # A source with nothing collected says so rather than being omitted.
    assert by_source["opera_rate"]["available"] is False


def test_status_reports_the_rolling_window_separately_from_the_age(client, stocked):
    """A 10-minute rolling maximum is not a snapshot, and says so."""
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert dbzh["window_minutes"] == 10.0
    assert dbzh["interval_minutes"] == 5.0


def test_status_carries_attribution_read_from_the_frame(client, stocked):
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert "MeteoFrance" in dbzh["attribution"]["producer"]
    assert dbzh["attribution"]["text"]


def test_status_ships_the_legend_so_the_client_cannot_drift(client, stocked):
    payload = client.get("/api/observed/status").json()
    dbzh = next(s for s in payload["sources"] if s["source"] == "opera_dbzh")
    assert dbzh["legend"]
    lightning = next(s for s in payload["sources"] if s["source"] == "eumetsat_li")
    # Lightning is points, not a raster — nothing to ramp.
    assert lightning["legend"] == []
    assert lightning["renders_imagery"] is False


# --- Overlay ---------------------------------------------------------------


def test_overlay_returns_a_png_with_its_own_valid_time(client, stocked):
    response = client.get("/api/observed/overlay/opera_dbzh.png", params=BBOX)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    # The badge on the map is fed by the same response that carries the image,
    # so the two cannot disagree.
    assert response.headers["X-Observed-Valid-Time"]
    assert response.headers["X-Observed-Attribution"]


def test_overlay_rejects_an_unknown_source(client, stocked):
    assert client.get("/api/observed/overlay/eumetsat_li.png", params=BBOX).status_code == 404
    assert client.get("/api/observed/overlay/nope.png", params=BBOX).status_code == 404


def test_overlay_rejects_an_empty_or_oversized_box(client, stocked):
    empty = dict(BBOX, north=BBOX["south"])
    assert client.get("/api/observed/overlay/opera_dbzh.png", params=empty).status_code == 400
    huge = {"south": 0.0, "west": 0.0, "north": 60.0, "east": 60.0}
    assert client.get("/api/observed/overlay/opera_dbzh.png", params=huge).status_code == 400


def test_overlay_says_410_when_nothing_current_is_held(client, observed_env):
    """Configured but empty is a different answer from "no such source"."""
    response = client.get("/api/observed/overlay/opera_dbzh.png", params=BBOX)
    assert response.status_code == 410


def test_a_stale_frame_is_not_served_as_current(client, observed_env):
    store = FrameStore(observed_env / "observed")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    store.write(
        SOURCE_OPERA_DBZH, old.replace(second=0, microsecond=0),
        (FIXTURES / "opera_dbzh.h5").read_bytes(), {},
    )
    assert client.get(
        "/api/observed/overlay/opera_dbzh.png", params=BBOX
    ).status_code == 410


def test_cloud_top_overlay_renders(client, stocked):
    response = client.get("/api/observed/overlay/eumetsat_ctth.png", params=BBOX)
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# --- Flashes ---------------------------------------------------------------


def test_flashes_come_back_as_points_with_individual_times(client, stocked):
    payload = client.get("/api/observed/flashes", params=BBOX).json()
    assert payload["count"] > 0
    first = payload["flashes"][0]
    assert {"lat", "lon", "time"} <= set(first)
    # Per-flash times are what lets the map fade by age instead of drawing a
    # ten-minute accumulation as one instant.
    times = {f["time"] for f in payload["flashes"]}
    assert len(times) > 1
    assert payload["window_minutes"] == 10.0


def test_flashes_outside_the_box_are_excluded(client, stocked):
    elsewhere = {"south": 40.0, "west": -8.0, "north": 42.0, "east": -6.0}
    payload = client.get("/api/observed/flashes", params=elsewhere).json()
    assert payload["count"] == 0
    # Absence of flashes is an observation, so the request still succeeds.
    assert payload["attribution"] or payload["newest_valid_time"]


# --- Tiled layers (#652) -----------------------------------------------------------


def _stamp(store, source):
    from weatherbrief.observed.frames import frame_stamp

    return frame_stamp(store.list_frames(source)[0].valid_time)


def test_frames_lists_radar_frames_with_a_tile_template(client, stocked):
    from weatherbrief.observed import tiles

    frame = stocked.list_frames(SOURCE_OPERA_DBZH)[0]
    tiles.write_canvas(stocked, SOURCE_OPERA_DBZH, frame.valid_time)
    response = client.get(f"/api/observed/frames/{SOURCE_OPERA_DBZH}")
    assert response.status_code == 200
    body = response.json()
    assert body["frames"] and body["frames"][0]["stamp"] == _stamp(stocked, SOURCE_OPERA_DBZH)
    assert body["tile_url_template"].endswith("/{stamp}/{z}/{x}/{y}.png")
    assert body["min_zoom"] < body["max_zoom"]
    assert body["stale"] is False
    assert body["attribution"]


def test_frames_rejects_a_source_that_is_not_tiled(client, stocked):
    assert client.get(f"/api/observed/frames/{SOURCE_EUMETSAT_CTTH}").status_code == 404


def test_tile_is_an_immutable_png(client, stocked):
    from weatherbrief.observed import tiles

    stamp = _stamp(stocked, SOURCE_OPERA_DBZH)
    x, y = tiles.lonlat_to_world_px(1.621, 50.517, 7)
    response = client.get(
        f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{stamp}/7/{int(x // 256)}/{int(y // 256)}.png"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "immutable" in response.headers["cache-control"]
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_tile_for_a_purged_frame_is_gone(client, stocked):
    response = client.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/20200101T0000/6/32/21.png")
    assert response.status_code == 410


def test_tile_rejects_bad_stamps_and_zooms(client, stocked):
    stamp = _stamp(stocked, SOURCE_OPERA_DBZH)
    assert client.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/nope/6/32/21.png").status_code == 400
    assert client.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{stamp}/14/0/0.png").status_code == 404
    assert client.get(f"/api/observed/tiles/{SOURCE_EUMETSAT_CTTH}/{stamp}/6/32/21.png").status_code == 404


def test_tiles_require_authentication(client_anon, stocked):
    stamp = _stamp(stocked, SOURCE_OPERA_DBZH)
    response = client_anon.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{stamp}/6/32/21.png")
    assert response.status_code in (401, 403)


@pytest.fixture
def satellite_times(monkeypatch):
    from weatherbrief.observed import satellite_ir

    times = [
        datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 3, 15, 50, tzinfo=timezone.utc),
    ]
    monkeypatch.setattr(satellite_ir, "available_times", lambda: list(times))
    fetched = []

    def fake_fetch(cycle, z, x, y):
        fetched.append((cycle, z, x, y))
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr(satellite_ir, "fetch_tile", fake_fetch)
    return fetched


def test_satellite_frames_come_from_the_advertised_cycles(client, satellite_times):
    body = client.get("/api/observed/frames/satellite_ir").json()
    assert [f["stamp"] for f in body["frames"]] == ["20261003T1600", "20261003T1550"]
    assert "EUMETSAT" in body["attribution"]["text"]


def test_satellite_tile_is_proxied_for_an_advertised_cycle(client, satellite_times):
    response = client.get("/api/observed/tiles/satellite_ir/20261003T1550/6/32/21.png")
    assert response.status_code == 200
    assert satellite_times == [(datetime(2026, 10, 3, 15, 50, tzinfo=timezone.utc), 6, 32, 21)]


def test_satellite_proxy_refuses_unadvertised_cycles(client, satellite_times):
    """Not a window onto EUMETView's whole archive."""
    response = client.get("/api/observed/tiles/satellite_ir/20250101T0000/6/32/21.png")
    assert response.status_code == 404
    assert satellite_times == []


def test_satellite_outage_is_503_not_500(client, monkeypatch):
    from weatherbrief.observed import satellite_ir

    def down():
        raise satellite_ir.SatelliteUnavailable("down")

    monkeypatch.setattr(satellite_ir, "available_times", down)
    assert client.get("/api/observed/frames/satellite_ir").status_code == 503


def test_satellite_can_be_switched_off(client, satellite_times, monkeypatch):
    monkeypatch.setenv("WB_SATELLITE_IR", "0")
    assert client.get("/api/observed/frames/satellite_ir").status_code == 404


def test_frames_wait_for_the_canvas_when_the_collector_builds_them(client, stocked):
    """A frame published a moment before its canvas must not be offered:
    every tile of it would otherwise build the canvas in the request path."""
    body = client.get(f"/api/observed/frames/{SOURCE_OPERA_DBZH}").json()
    assert body["frames"] == []


def test_frames_list_everything_when_canvases_are_built_lazily(client, stocked, monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_TILES", "0")
    body = client.get(f"/api/observed/frames/{SOURCE_OPERA_DBZH}").json()
    assert [f["stamp"] for f in body["frames"]] == [_stamp(stocked, SOURCE_OPERA_DBZH)]


def test_a_canvas_being_built_is_a_retryable_503(client, stocked, monkeypatch):
    from weatherbrief.observed import tiles

    def busy(*_a, **_k):
        raise tiles.CanvasBusy("building")

    monkeypatch.setattr(tiles, "tile_png", busy)
    stamp = _stamp(stocked, SOURCE_OPERA_DBZH)
    response = client.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{stamp}/6/32/21.png")
    assert response.status_code == 503
    assert response.headers.get("retry-after") == "2"


def test_an_unbuildable_frame_is_gone_not_a_500(client, stocked, monkeypatch):
    from weatherbrief.observed import tiles

    def broken(*_a, **_k):
        raise tiles.CanvasUnavailable("corrupt")

    monkeypatch.setattr(tiles, "tile_png", broken)
    stamp = _stamp(stocked, SOURCE_OPERA_DBZH)
    response = client.get(f"/api/observed/tiles/{SOURCE_OPERA_DBZH}/{stamp}/6/32/21.png")
    assert response.status_code == 410


def test_a_young_satellite_cycle_is_cached_briefly(client, satellite_times, monkeypatch):
    """Advertised before complete? Then not pinned in the browser for a day."""
    from weatherbrief.observed import satellite_ir

    monkeypatch.setattr(satellite_ir, "is_young", lambda cycle, now=None: True)
    response = client.get("/api/observed/tiles/satellite_ir/20261003T1600/6/32/21.png")
    assert "immutable" not in response.headers["cache-control"]
    monkeypatch.setattr(satellite_ir, "is_young", lambda cycle, now=None: False)
    response = client.get("/api/observed/tiles/satellite_ir/20261003T1550/6/32/21.png")
    assert "immutable" in response.headers["cache-control"]


# --- Cell overlay (#656) --------------------------------------------------------


@pytest.fixture
def cells_store(observed_env, monkeypatch):
    """Ingest enabled, with two overlays in the store: 10 and 15 minutes old."""
    from weatherbrief.observed import cells_display
    from weatherbrief.observed.frames import frame_stamp

    from observed.cells_helpers import display_bytes, display_cell_doc, display_doc

    monkeypatch.setenv("WB_CELLS_INGEST_ENABLED", "1")
    store = cells_display.DisplayStore(observed_env / "observed" / "cells" / "display")
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % 5)
    stamps = []
    for minutes in (10, 15):
        t = now - timedelta(minutes=minutes)
        cells = [display_cell_doc("core41-in", 50.5, 1.5), display_cell_doc("core35-far", 45.0, 10.0, tier="core35")]
        store.write(frame_stamp(t), display_bytes(display_doc(t, cells=cells)))
        stamps.append(frame_stamp(t))
    return stamps


def test_cell_frames_say_disabled_without_the_flag(client, monkeypatch):
    monkeypatch.setenv("WB_CELLS_INGEST_ENABLED", "0")
    body = client.get("/api/observed/cells/frames").json()
    assert body["enabled"] is False and body["frames"] == []
    assert client.get("/api/observed/cells/20261004T1200.json").status_code == 404


def test_cell_frames_list_newest_first_with_received_at(client, cells_store):
    body = client.get("/api/observed/cells/frames").json()
    assert body["enabled"] is True and body["stale"] is False
    assert [f["stamp"] for f in body["frames"]] == cells_store
    assert all("received_at" in f and "valid_time" in f for f in body["frames"])
    assert body["url_template"] == "/api/observed/cells/{stamp}.json"


def test_cell_frames_report_stale_with_unavailable_since(client, observed_env, monkeypatch):
    from weatherbrief.observed import cells_display
    from weatherbrief.observed.frames import frame_stamp

    from observed.cells_helpers import display_bytes, display_doc

    monkeypatch.setenv("WB_CELLS_INGEST_ENABLED", "1")
    store = cells_display.DisplayStore(observed_env / "observed" / "cells" / "display")
    t = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)
    t -= timedelta(minutes=t.minute % 5)
    store.write(frame_stamp(t), display_bytes(display_doc(t)))
    body = client.get("/api/observed/cells/frames").json()
    assert body["stale"] is True
    assert body["unavailable_since"] == t.isoformat()


def test_cell_display_is_immutable_json(client, cells_store):
    response = client.get(f"/api/observed/cells/{cells_store[0]}.r0.json")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    body = response.json()
    assert {c["id"] for c in body["cells"]} == {"core41-in", "core35-far"}


def test_an_amended_frame_is_listed_at_its_newest_revision(client, cells_store, observed_env):
    """#666: lightning landing later re-issues a frame as r1.  The listing
    points at r1; r0 stays served (immutable) for anyone holding the old
    listing; a bare stamp (a pre-#666 client) gets r1, never cached."""
    from weatherbrief.observed import cells_display
    from weatherbrief.observed.frames import parse_frame_stamp

    from observed.cells_helpers import display_bytes, display_cell_doc, display_doc

    store = cells_display.DisplayStore(observed_env / "observed" / "cells" / "display")
    stamp = cells_store[0]
    doc = display_doc(parse_frame_stamp(stamp), cells=[display_cell_doc("core41-in", 50.5, 1.5)])
    store.write(stamp, display_bytes({**doc, "revision": 1}), revision=1)

    body = client.get("/api/observed/cells/frames").json()
    first = body["frames"][0]
    assert first["stamp"] == stamp and first["key"] == f"{stamp}.r1" and first["revision"] == 1
    assert body["frames"][1]["key"] == f"{cells_store[1]}.r0"
    assert [f["stamp"] for f in body["frames"]] == cells_store  # one entry per frame

    r1 = client.get(f"/api/observed/cells/{stamp}.r1.json")
    assert "immutable" in r1.headers["cache-control"] and len(r1.json()["cells"]) == 1
    r0 = client.get(f"/api/observed/cells/{stamp}.r0.json")
    assert "immutable" in r0.headers["cache-control"] and len(r0.json()["cells"]) == 2
    bare = client.get(f"/api/observed/cells/{stamp}.json")
    assert bare.headers["cache-control"] == "no-cache" and len(bare.json()["cells"]) == 1
    assert client.get(f"/api/observed/cells/{stamp}.r2.json").status_code == 410


def test_cell_display_clips_to_the_route_box(client, cells_store):
    body = client.get(f"/api/observed/cells/{cells_store[0]}.json", params=BBOX).json()
    assert [c["id"] for c in body["cells"]] == ["core41-in"]
    assert body["outlines"]["core35"] == []


def test_cell_display_rejects_partial_or_empty_boxes_and_bad_stamps(client, cells_store):
    url = f"/api/observed/cells/{cells_store[0]}.json"
    assert client.get(url, params={"south": 1, "west": 2}).status_code == 400
    assert client.get(url, params={"south": 51, "west": 0, "north": 50, "east": 2}).status_code == 400
    assert client.get("/api/observed/cells/notastamp.json").status_code == 400
    assert client.get(f"/api/observed/cells/{cells_store[0]}.rx.json").status_code == 400
    # Wider than any route corridor (and than the "Now" map's flash box).
    huge = {"south": -80, "west": -170, "north": 80, "east": 170}
    assert client.get(url, params=huge).status_code == 400


def test_cell_display_for_a_purged_stamp_is_gone(client, cells_store):
    assert client.get("/api/observed/cells/20200101T0000.json").status_code == 410


def test_cell_endpoints_require_authentication(client_anon, cells_store):
    assert client_anon.get("/api/observed/cells/frames").status_code in (401, 403)
    assert client_anon.get(f"/api/observed/cells/{cells_store[0]}.json").status_code in (401, 403)


def test_whole_europe_overlay_is_served_as_the_stored_gzip(client, cells_store, observed_env):
    path = observed_env / "observed" / "cells" / "display" / f"{cells_store[0]}.json.gz"
    response = client.get(f"/api/observed/cells/{cells_store[0]}.json", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    import gzip as _gzip
    import json as _json
    assert response.json() == _json.loads(_gzip.decompress(path.read_bytes()))


def test_overlay_without_gzip_support_is_plain_json(client, cells_store):
    response = client.get(f"/api/observed/cells/{cells_store[0]}.json", headers={"Accept-Encoding": "identity"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert len(response.json()["cells"]) == 2


def test_flashes_accept_a_europe_wide_box_for_the_now_map(client, stocked):
    europe = {"south": 30.0, "west": -25.0, "north": 72.0, "east": 45.0}
    payload = client.get("/api/observed/flashes", params=europe).json()
    assert payload["count"] > 0
    # Imagery keeps its corridor-sized limit.
    assert client.get("/api/observed/overlay/opera_dbzh.png", params=europe).status_code == 400


def test_flashes_trail_can_be_shortened(client, stocked):
    full = client.get("/api/observed/flashes", params={**BBOX, "minutes": 60}).json()
    assert full["count"] > 0
    none = client.get("/api/observed/flashes", params={**BBOX, "minutes": 0.001}).json()
    assert none["count"] == 0
    assert client.get("/api/observed/flashes", params={**BBOX, "minutes": 0}).status_code == 422


def test_a_wide_flash_box_without_minutes_gets_the_short_trail(client, stocked, monkeypatch):
    # The full retained trail over a continent could be several MB; a caller
    # that forgets `minutes` gets the "Now" tab's trail, a corridor does not.
    from weatherbrief.api import observed as observed_api
    monkeypatch.setattr(observed_api, "WIDE_FLASH_TRAIL_MINUTES", 0.001)
    europe = {"south": 30.0, "west": -25.0, "north": 72.0, "east": 45.0}
    assert client.get("/api/observed/flashes", params=europe).json()["count"] == 0
    assert client.get("/api/observed/flashes", params=BBOX).json()["count"] > 0
    assert client.get("/api/observed/flashes", params={**europe, "minutes": 600}).json()["count"] > 0
