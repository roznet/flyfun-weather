"""Collector behaviour: deterministic keys, no re-fetch, purge on every tick.

Network access is faked throughout.  The one test that talks to the real
providers is gated behind ``WB_OBSERVED_LIVE_TESTS=1`` — see
``test_collect_live.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.observed import collect
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    FrameStore,
)

NOW = datetime(2026, 8, 25, 14, 7, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> FrameStore:
    return FrameStore(tmp_path / "observed")


class _Response:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _Session:
    """Serves fixture bytes for any key, recording what was asked for."""

    def __init__(self, payload: bytes, status: int = 200):
        self.payload = payload
        self.status = status
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return _Response(self.status, self.payload)


# --- Frame-time arithmetic -------------------------------------------------


def test_expected_times_are_on_the_cadence_newest_first():
    times = collect.expected_frame_times(SOURCE_OPERA_DBZH, NOW, lookback=timedelta(minutes=20))
    assert times == sorted(times, reverse=True)
    assert all(t.minute % 5 == 0 for t in times)
    # 14:07 minus the 4-minute delivery lag, floored to the 5-minute slot.
    assert times[0] == datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)


def test_delivery_lag_keeps_us_from_asking_too_early():
    """Requesting a frame before it can exist just spends a 404."""
    just_after = datetime(2026, 8, 25, 14, 5, 30, tzinfo=timezone.utc)
    times = collect.expected_frame_times(SOURCE_OPERA_DBZH, just_after)
    assert datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc) not in times


def test_rate_uses_its_own_fifteen_minute_cadence():
    times = collect.expected_frame_times(SOURCE_OPERA_RATE, NOW, lookback=timedelta(hours=1))
    assert all(t.minute % 15 == 0 for t in times)


def test_lookback_bounds_the_backfill():
    """An outage must not trigger a fetch storm for frames about to be purged."""
    times = collect.expected_frame_times(
        SOURCE_OPERA_DBZH, NOW, lookback=timedelta(minutes=30)
    )
    assert len(times) == 7  # 14:00 back to 13:30 on the 5-minute cadence
    assert min(times) == datetime(2026, 8, 25, 13, 30, tzinfo=timezone.utc)


# --- OPERA endpoint --------------------------------------------------------


def test_opera_endpoint_is_cloudferro_not_aws():
    """The ORD open cache is hosted by CloudFerro; the bucket name is not enough.

    `https://openradar-24h.s3.amazonaws.com` answers `NoSuchBucket`, and a 404
    is deliberately read as "not published yet" — so an AWS-shaped URL makes
    the entire radar half of the feature collect nothing, forever, without
    logging anything. Every other collector test injects a fake session, so
    nothing else in the suite ever looks at the host.
    """
    assert "cloudferro" in collect.OPERA_BASE_URL
    assert "amazonaws" not in collect.OPERA_BASE_URL
    assert collect.OPERA_BASE_URL.startswith("https://")


def test_collect_requests_the_deterministic_key_on_that_endpoint(store, dbzh_path):
    session = _Session(dbzh_path.read_bytes())
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1, session=session,
        lookback=timedelta(minutes=10),
    )
    assert session.urls
    url = session.urls[0]
    assert url.startswith(collect.OPERA_BASE_URL + "/")
    # …/YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@DBZH.h5
    assert url.endswith("/2026/08/25/OPERA/COMP/OPERA@20260825T1400@0@DBZH.h5")


def test_a_wholly_missing_source_is_logged_not_silent(store, caplog):
    """Every slot 404 with nothing stored is a broken source, not a quiet one."""
    session = _Session(b"", status=404)
    with caplog.at_level("WARNING"):
        result = collect.collect_opera(
            SOURCE_OPERA_DBZH, store, now=NOW, session=session,
            lookback=timedelta(minutes=30),
        )
    assert result.fetched == 0 and result.missing > 0
    assert any("all" in r.message and "missing" in r.message for r in caplog.records), (
        "a totally dead radar path must say so — this is the failure mode that "
        "let a wrong endpoint ship undetected"
    )


# --- OPERA fetch -----------------------------------------------------------


def test_collect_fetches_missing_frames(store, dbzh_path):
    session = _Session(dbzh_path.read_bytes())
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=2, session=session,
        lookback=timedelta(minutes=15),
    )
    assert result.fetched == 2
    assert result.bytes_in > 0
    assert len(session.urls) == 2
    assert all("/OPERA/COMP/OPERA@" in url for url in session.urls)
    assert len(store.list_frames(SOURCE_OPERA_DBZH)) == 2


def test_collect_never_refetches_a_held_frame(store, dbzh_path):
    payload = dbzh_path.read_bytes()
    first = _Session(payload)
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=4, session=first,
        lookback=timedelta(minutes=15),
    )
    second = _Session(payload)
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=4, session=second,
        lookback=timedelta(minutes=15),
    )
    assert result.fetched == 0
    assert result.skipped > 0
    assert second.urls == []


def test_sidecar_is_built_from_the_downloaded_frame(store, dbzh_path):
    """Attribution is read out of the payload, not stamped from a constant."""
    session = _Session(dbzh_path.read_bytes())
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1, session=session,
        lookback=timedelta(0),
    )
    frame = store.list_frames(SOURCE_OPERA_DBZH)[0]
    assert "MeteoFrance" in frame.attribution.producer
    assert frame.meta["grid"]["nx"] == 160
    assert frame.meta["window_minutes"] == pytest.approx(10.0)


def test_not_published_yet_is_not_an_error(store):
    session = _Session(b"", status=404)
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, session=session,
        lookback=timedelta(minutes=15),
    )
    assert result.missing > 0
    assert result.failed == 0
    assert result.errors == []


def test_unreadable_payload_is_discarded_not_published(store):
    """A truncated download must not become a frame the sampler will open."""
    session = _Session(b"not an hdf5 file")
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1, session=session,
        lookback=timedelta(0),
    )
    assert result.fetched == 0
    assert result.failed == 1
    assert store.list_frames(SOURCE_OPERA_DBZH) == []
    # And nothing is left behind on disk to be purged later.
    assert not any(store.source_dir(SOURCE_OPERA_DBZH).glob("*.h5"))


def test_every_tick_purges(store, dbzh_path):
    old = NOW - timedelta(hours=5)
    store.write(SOURCE_OPERA_DBZH, old, b"x", {})
    session = _Session(dbzh_path.read_bytes())
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1, session=session,
        lookback=timedelta(0),
    )
    assert result.purged == 1
    assert old not in [f.valid_time for f in store.list_frames(SOURCE_OPERA_DBZH)]


# --- Scheduling ------------------------------------------------------------


def test_due_sources_lists_only_what_is_missing(store, dbzh_path):
    sources = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)
    assert set(collect.due_sources(store, now=NOW, sources=sources)) == set(sources)

    session = _Session(dbzh_path.read_bytes())
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1, session=session,
        lookback=timedelta(0),
    )
    assert collect.due_sources(store, now=NOW, sources=sources) == [SOURCE_OPERA_RATE]


def test_one_source_failing_does_not_stop_the_others(store, monkeypatch):
    def _boom(source, *args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(collect, "collect_opera", _boom)
    monkeypatch.setattr(
        collect, "collect_eumetsat",
        lambda source, store, **kwargs: collect.CollectResult(source=source, fetched=1),
    )
    results = collect.collect_once(
        store, now=NOW, sources=(SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_CTTH)
    )
    by_source = {r.source: r for r in results}
    assert by_source[SOURCE_OPERA_DBZH].failed == 1
    assert by_source[SOURCE_EUMETSAT_CTTH].fetched == 1


# --- Configuration ---------------------------------------------------------


def test_collection_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    assert collect.observed_enabled() is False
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    assert collect.observed_enabled() is True


def test_source_selection_can_be_narrowed(monkeypatch):
    """Radar without EUMETSAT credentials is half the feature, not a broken one."""
    monkeypatch.setenv("WB_OBSERVED_SOURCES", "opera_dbzh, opera_rate")
    assert collect.enabled_sources() == (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)


def test_unknown_source_selection_fails_loudly(monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_SOURCES", "opera_dbhz")  # typo
    with pytest.raises(ValueError, match="Unknown observed sources"):
        collect.enabled_sources()


# --- EUMETSAT --------------------------------------------------------------


class _Product:
    def __init__(self, payload: bytes, sensing_end: datetime):
        self._payload = payload
        self.sensing_end = sensing_end

    def open(self):
        import contextlib
        import io

        return contextlib.closing(io.BytesIO(self._payload))

    def __str__(self):
        return f"product-{self.sensing_end:%H%M}"


class _Collection:
    def __init__(self, products):
        self._products = products

    def search(self, dtstart=None, dtend=None):
        return list(self._products)


class _DataStore:
    def __init__(self, collection):
        self._collection = collection

    def get_collection(self, _identifier):
        return self._collection


def _zip_of(path) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("quicklooks/QCK-IMAGE-CTT--PNG.png", b"not the granule")
        archive.writestr("granule.nc", path.read_bytes())
    return buffer.getvalue()


def test_eumetsat_product_is_unwrapped_and_snapped(store, ctth_path):
    product = _Product(
        _zip_of(ctth_path),
        datetime(2026, 8, 25, 14, 3, 47, tzinfo=timezone.utc),
    )
    result = collect.collect_eumetsat(
        SOURCE_EUMETSAT_CTTH, store, now=NOW,
        datastore=_DataStore(_Collection([product])),
    )
    assert result.fetched == 1
    frames = store.list_frames(SOURCE_EUMETSAT_CTTH)
    # Sensing times carry seconds; the store keys on the cadence slot so a
    # second tick recognises the same granule instead of re-fetching it.
    assert frames[0].valid_time == datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
    # The quicklook PNGs are dropped — only the netCDF is kept.
    assert frames[0].path.read_bytes() == ctth_path.read_bytes()


def test_eumetsat_search_failure_still_purges(store):
    old = NOW - timedelta(hours=5)
    store.write(SOURCE_EUMETSAT_CTTH, old, b"x", {})

    class _Broken:
        def get_collection(self, _identifier):
            raise RuntimeError("token expired")

    result = collect.collect_eumetsat(
        SOURCE_EUMETSAT_CTTH, store, now=NOW, datastore=_Broken()
    )
    assert result.failed == 1
    assert result.purged == 1
