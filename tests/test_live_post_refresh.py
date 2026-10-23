"""The live layer built right after a full refresh (#898): the window gate,
the skips, never raising, and the call before the refresh reports complete.

Commit ordering against the tick (older-pack refusal, stale computation
dropped) is commit_live_update's, tested in tests/test_live_layer.py.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import test_live_tick as tt


@pytest.fixture
def wired(db_session, dev_user, tmp_path, monkeypatch):
    """A flight with a pack on disk, the test's own session, and the refresh
    and highlight replaced by recorders."""
    from weatherbrief.tasks import live_highlight, live_layer, live_tick, route_weather

    pack_dir = tt._write_pack(tmp_path)
    # The test's own session, never committed: the fixture rolls back.
    monkeypatch.setattr("flyfun_common.db.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "commit", lambda: None)
    monkeypatch.setattr(live_tick, "live_enabled", lambda: True)
    monkeypatch.setattr(live_tick, "_cloud_source", lambda db, f: None)
    refresh = MagicMock()
    monkeypatch.setattr(route_weather, "run_realtime_refresh", refresh)
    layer = SimpleNamespace(glance=None)
    monkeypatch.setattr(live_layer, "live_for_pack", lambda _d: layer)
    monkeypatch.setattr(live_highlight, "highlight_enabled", lambda: True)
    highlights = []
    monkeypatch.setattr(live_highlight, "ensure_highlight",
                        lambda d, lay, **kw: highlights.append(kw) or live_highlight.HighlightOutcome("written"))
    monkeypatch.setattr(live_highlight, "charge_highlight", lambda *a, **k: None)

    def flight(departure):
        tt._flight(db_session, dev_user, "zz-soon", departure, artifact_path=str(pack_dir))

    return SimpleNamespace(pack_dir=pack_dir, refresh=refresh, highlights=highlights, flight=flight)


def test_in_window_refresh_writes_the_layer_and_highlight(wired):
    from weatherbrief.tasks.live_tick import run_post_refresh_update

    wired.flight(tt.NOW + timedelta(hours=1))
    stages = []
    assert run_post_refresh_update(
        "zz-soon", "/fake/db", now=tt.NOW, progress_callback=lambda stage: stages.append(stage),
    ) is True
    assert stages == ["live_observations"]
    assert wired.refresh.call_args.args == (wired.pack_dir, "/fake/db")
    assert wired.refresh.call_args.kwargs["flight_id"] == "zz-soon"
    assert "report_source" not in wired.refresh.call_args.kwargs  # a fresh fetch
    # The ordinary gate, not Start's re-timed one.
    assert wired.highlights == [{"retimed_from": None}]


def test_outside_window_writes_nothing(wired):
    from weatherbrief.tasks.live_tick import run_post_refresh_update

    wired.flight(tt.NOW + timedelta(hours=5))  # window opens 3 h before
    stages = []
    assert run_post_refresh_update(
        "zz-soon", "/fake/db", now=tt.NOW, progress_callback=lambda stage: stages.append(stage),
    ) is False
    wired.refresh.assert_not_called()
    assert stages == []  # no "Updating live observations" step outside the window


def test_as_of_refresh_writes_nothing(wired):
    from weatherbrief.tasks.live_tick import run_post_refresh_update

    wired.flight(tt.NOW + timedelta(hours=1))
    assert run_post_refresh_update("zz-soon", "/fake/db", as_of=tt.NOW, now=tt.NOW) is False
    wired.refresh.assert_not_called()


def test_frozen_layer_writes_nothing(wired):
    from weatherbrief.tasks.live_layer import LIVE_FROZEN_FILE
    from weatherbrief.tasks.live_tick import run_post_refresh_update

    wired.flight(tt.NOW + timedelta(hours=1))
    (wired.pack_dir.parent / LIVE_FROZEN_FILE).write_text("imported\n")
    assert run_post_refresh_update("zz-soon", "/fake/db", now=tt.NOW) is False
    wired.refresh.assert_not_called()


def test_disabled_layer_writes_nothing(wired, monkeypatch):
    from weatherbrief.tasks import live_tick

    wired.flight(tt.NOW + timedelta(hours=1))
    monkeypatch.setattr(live_tick, "live_enabled", lambda: False)
    assert live_tick.run_post_refresh_update("zz-soon", "/fake/db", now=tt.NOW) is False
    wired.refresh.assert_not_called()


def test_failed_refresh_is_logged_once_and_never_raises(wired, caplog):
    from weatherbrief.tasks.live_tick import run_post_refresh_update

    wired.flight(tt.NOW + timedelta(hours=1))
    wired.refresh.side_effect = RuntimeError("aviationweather down")
    with caplog.at_level("INFO", logger="weatherbrief.tasks.live_tick"):
        assert run_post_refresh_update("zz-soon", "/fake/db", now=tt.NOW) is False
    lines = [r.getMessage() for r in caplog.records if "LIVE_POST_REFRESH" in r.getMessage()]
    assert lines == ["LIVE_POST_REFRESH flight=zz-soon outcome=not_refreshed"]


def test_never_raises_when_the_db_is_down(monkeypatch):
    from weatherbrief.tasks import live_tick

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(live_tick, "live_enabled", lambda: True)
    monkeypatch.setattr("flyfun_common.db.SessionLocal", boom)
    assert live_tick.run_post_refresh_update("zz", "/fake/db", now=tt.NOW) is False


def test_refresh_wrapper_passes_progress_and_skips_as_of(monkeypatch):
    """The shared post-commit helper every full-refresh path calls hands its
    progress callback on, and does nothing for an as-of refresh."""
    from weatherbrief.api import packs
    from weatherbrief.tasks import live_tick

    calls = []
    progress = lambda stage, detail=None: None  # noqa: E731
    monkeypatch.setattr(live_tick, "live_enabled", lambda: True)
    monkeypatch.setattr(live_tick, "run_post_refresh_update",
                        lambda fid, db, **kw: calls.append((fid, db, kw)))
    packs._post_refresh_live_update("zz", "/fake/db", as_of_time=None, progress_callback=progress)
    assert calls == [("zz", "/fake/db", {"as_of": None, "progress_callback": progress})]
    assert "live_observations" in packs._STAGE_LABELS

    calls.clear()
    packs._post_refresh_live_update("zz", "/fake/db", as_of_time=tt.NOW, progress_callback=progress)
    assert calls == []
