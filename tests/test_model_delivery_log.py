"""Durable model-delivery log (issue #515).

Three layers under test:

* ``storage/model_delivery`` — the insert, its idempotency on
  ``(source, cycle_init)``, and the best-effort wrapper's refusal to raise;
* ``scheduler._run_freshness_check_once`` — a row is written exactly when a
  marker advances, carrying the registry expectation *as it was*, the
  provider's publish time, our detection time, and the last probe that
  didn't see the run;
* ``Marker.last_probe_at`` and the ``marker_health="unobserved"`` it enables.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from weatherbrief.db.models import ModelDeliveryLogRow
from weatherbrief.fetch.freshness import catalog, registry
from weatherbrief.fetch.freshness.markers import MarkerStore
from weatherbrief.fetch.freshness.sources import (
    VIA_HTTP_LAST_MODIFIED,
    VIA_OM_META,
    VIA_SENTINEL_MTIME,
    Observation,
)
from weatherbrief.storage import model_delivery

SOURCE = "gfs:noaa"
MODEL = "gfs"


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def delivery_db(monkeypatch):
    """In-memory DB wired as the storage module's ``SessionLocal``.

    ``record_delivery`` opens its own session (it runs off the freshness
    loop, which holds none), so the module-level symbol is what has to point
    at the test engine.
    """
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(model_delivery, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


def _rows(session_factory) -> list[ModelDeliveryLogRow]:
    s = session_factory()
    try:
        return s.query(ModelDeliveryLogRow).order_by(ModelDeliveryLogRow.id).all()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------


class TestRecordDelivery:
    def test_writes_one_row_with_every_column(self, delivery_db):
        model_delivery.record_delivery(
            source=SOURCE,
            model=MODEL,
            cycle_init=_utc(2026, 7, 30, 6),
            expected_at=_utc(2026, 7, 30, 11, 30),
            published_at=_utc(2026, 7, 30, 13, 47),
            detected_at=_utc(2026, 7, 30, 13, 50),
            last_absent_at=_utc(2026, 7, 30, 13, 45),
            observed_via=VIA_HTTP_LAST_MODIFIED,
        )
        rows = _rows(delivery_db)
        assert len(rows) == 1
        row = rows[0]
        assert row.source == SOURCE
        assert row.model == MODEL
        assert row.observed_via == VIA_HTTP_LAST_MODIFIED
        # Derived-on-read, never stored: this run was 2h17m later than the
        # registry predicted, and we noticed 3 min after it published.
        assert row.published_at - row.expected_at == timedelta(hours=2, minutes=17)
        assert row.detected_at - row.published_at == timedelta(minutes=3)
        # The absence bracket must sit before the publish time it brackets.
        assert row.last_absent_at < row.published_at

    def test_second_observation_of_same_run_is_a_no_op(self, delivery_db):
        """``UNIQUE (source, cycle_init)`` — re-observing a run must not
        append a duplicate, and must keep the *first* (tightest) bracket."""
        for detected_minute in (50, 55):
            model_delivery.record_delivery(
                source=SOURCE,
                model=MODEL,
                cycle_init=_utc(2026, 7, 30, 6),
                expected_at=_utc(2026, 7, 30, 11, 30),
                published_at=_utc(2026, 7, 30, 13, 47),
                detected_at=_utc(2026, 7, 30, 13, detected_minute),
                last_absent_at=_utc(2026, 7, 30, 13, 45),
                observed_via=VIA_HTTP_LAST_MODIFIED,
            )
        rows = _rows(delivery_db)
        assert len(rows) == 1
        assert rows[0].detected_at == _utc(2026, 7, 30, 13, 50)

    def test_same_cycle_from_a_different_source_is_a_separate_row(self, delivery_db):
        """Uniqueness is per (source, cycle_init) — every model publishes an
        06Z run and they are independent measurements."""
        for source, model, via in (
            (SOURCE, MODEL, VIA_HTTP_LAST_MODIFIED),
            ("ecmwf:direct", "ecmwf", VIA_SENTINEL_MTIME),
            ("gfs:openmeteo", "gfs", VIA_OM_META),
        ):
            model_delivery.record_delivery(
                source=source,
                model=model,
                cycle_init=_utc(2026, 7, 30, 6),
                expected_at=_utc(2026, 7, 30, 11, 30),
                detected_at=_utc(2026, 7, 30, 13, 50),
                observed_via=via,
            )
        assert len(_rows(delivery_db)) == 3

    def test_null_published_at_still_records_the_bracket(self, delivery_db):
        """A missing provider timestamp doesn't make the observation
        worthless: ``(last_absent_at, detected_at]`` still bounds arrival."""
        model_delivery.record_delivery(
            source="ecmwf:direct",
            model="ecmwf",
            cycle_init=_utc(2026, 7, 30, 0),
            expected_at=_utc(2026, 7, 30, 6, 40),
            published_at=None,
            detected_at=_utc(2026, 7, 30, 7, 5),
            last_absent_at=_utc(2026, 7, 30, 7, 0),
            observed_via=VIA_SENTINEL_MTIME,
        )
        row = _rows(delivery_db)[0]
        assert row.published_at is None
        assert row.last_absent_at == _utc(2026, 7, 30, 7, 0)

    def test_db_failure_never_escapes(self, monkeypatch):
        """Telemetry must not be able to fail a freshness tick."""

        def _boom():
            raise RuntimeError("db is down")

        monkeypatch.setattr(model_delivery, "SessionLocal", _boom)
        # No raise, no rows — the sample is simply lost.
        model_delivery.record_delivery(
            source=SOURCE,
            model=MODEL,
            cycle_init=_utc(2026, 7, 30, 6),
            expected_at=_utc(2026, 7, 30, 11, 30),
            detected_at=_utc(2026, 7, 30, 13, 50),
            observed_via=VIA_HTTP_LAST_MODIFIED,
        )


# ---------------------------------------------------------------------------
# Scheduler wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def loop_env(monkeypatch, delivery_db):
    """Freshness loop pinned to one source, a fixed wallclock, and a stub check.

    Returns a small handle so each test can set the wallclock and what
    ``check_source`` reports, then drive a tick.
    """
    import weatherbrief.scheduler as scheduler_mod
    from weatherbrief.fetch.freshness import markers as markers_mod
    from weatherbrief.fetch.freshness import sources as sources_mod

    class _Env:
        now = _utc(2026, 7, 30, 12)
        observation: Observation | None = None
        store = MarkerStore()

        async def tick(self):
            await scheduler_mod._run_freshness_check_once()

        def marker(self):
            return self.store.get_sync(SOURCE, MODEL)

    env = _Env()

    class _FakeDatetime:
        @staticmethod
        def now(tz):
            return env.now

    monkeypatch.setattr(markers_mod, "_STORE", env.store)
    monkeypatch.setattr(scheduler_mod, "datetime", _FakeDatetime)
    monkeypatch.setattr(
        sources_mod, "all_tracked_sources", lambda: [(SOURCE, MODEL)],
    )
    monkeypatch.setattr(
        sources_mod, "check_source", lambda source, model: env.observation,
    )
    return env


@pytest.mark.asyncio
async def test_advance_writes_a_delivery_row(loop_env, delivery_db):
    env = loop_env
    await env.store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
    before = env.marker()

    # A probe that confirms the bootstrapped init — no advance, no row, but
    # it stamps the probe time that will become the next row's lower bound.
    env.now = before.next_expected
    env.observation = Observation(
        init=before.init, published_at=None, observed_via=VIA_HTTP_LAST_MODIFIED,
    )
    await env.tick()
    assert _rows(delivery_db) == []
    assert env.marker().last_probe_at == before.next_expected

    # Now the next cycle lands.
    new_init = registry.next_cycle_init_after(SOURCE, before.init)
    env.now = env.marker().next_expected + timedelta(minutes=20)
    published = env.now - timedelta(minutes=4)
    env.observation = Observation(
        init=new_init, published_at=published, observed_via=VIA_HTTP_LAST_MODIFIED,
    )
    await env.tick()

    rows = _rows(delivery_db)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == SOURCE
    assert row.model == MODEL
    assert row.cycle_init == new_init
    assert row.published_at == published
    assert row.detected_at == env.now
    assert row.observed_via == VIA_HTTP_LAST_MODIFIED
    # Expectation is the registry's, frozen at observation time.
    assert row.expected_at == registry.expected_delivery_for_init(SOURCE, new_init)
    # The lower bound is the earlier probe that didn't see this run — not the
    # heartbeat, which the loop bumps on every tick.
    assert row.last_absent_at == before.next_expected


@pytest.mark.asyncio
async def test_slip_writes_no_row(loop_env, delivery_db):
    """Only an advance is an observed delivery. A run that hasn't shown up
    is a fact about the schedule, reconstructed by joining against the cycle
    grid — storing a synthetic row would assert a schedule we may stop
    believing."""
    env = loop_env
    await env.store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
    before = env.marker()

    env.now = before.next_expected + timedelta(minutes=5)
    env.observation = Observation(
        init=before.init, published_at=None, observed_via=VIA_HTTP_LAST_MODIFIED,
    )
    await env.tick()

    assert env.marker().slip_count == 1
    assert _rows(delivery_db) == []


@pytest.mark.asyncio
async def test_failed_check_writes_no_row_and_no_probe(loop_env, delivery_db):
    """A check that couldn't reach the provider is not evidence of absence,
    so it must not move the bracket that ``last_absent_at`` will report."""
    env = loop_env
    await env.store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))

    env.now = env.marker().next_expected + timedelta(minutes=5)
    env.observation = None
    await env.tick()

    assert _rows(delivery_db) == []
    assert env.marker().last_probe_at is None
    # The heartbeat still moves — a transient failure shouldn't force every
    # freshness HTTP call into the inline-fallback path.
    assert env.marker().last_check == env.now


@pytest.mark.asyncio
async def test_repeated_advance_of_the_same_run_writes_one_row(loop_env, delivery_db):
    """A restart re-bootstraps markers from the registry, so the same run can
    legitimately be 'advanced onto' twice. The unique constraint absorbs it."""
    env = loop_env
    await env.store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
    new_init = registry.next_cycle_init_after(SOURCE, env.marker().init)

    for minute in (20, 25):
        env.store = MarkerStore()
        from weatherbrief.fetch.freshness import markers as markers_mod
        markers_mod._STORE = env.store
        await env.store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
        env.now = env.marker().next_expected + timedelta(minutes=minute)
        env.observation = Observation(
            init=new_init, published_at=None, observed_via=VIA_HTTP_LAST_MODIFIED,
        )
        await env.tick()

    assert len(_rows(delivery_db)) == 1


# ---------------------------------------------------------------------------
# marker_health: "ok" must not mean "never observed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrapped_but_unprobed_marker_reports_unobserved():
    """The prod symptom: ``gem:openmeteo`` showed a 36h-stale ``latest_init``
    and a null ``published_at`` under a green ``marker_health="ok"``, because
    ``is_stale`` measures time since the last check *attempt* and the loop
    bumps that even on ticks that do no I/O."""
    store = MarkerStore()
    await store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
    # Loop interval large enough that the fixed bootstrap wallclock can never
    # read as a stale heartbeat, isolating the unobserved signal.
    entries = {
        e.key: e for e in catalog.build(store=store, loop_interval_s=86400 * 36500)
    }
    assert entries[SOURCE].marker_health == "unobserved"


@pytest.mark.asyncio
async def test_marker_health_ok_once_a_probe_succeeds():
    store = MarkerStore()
    await store.bootstrap([(SOURCE, MODEL)], now=_utc(2026, 7, 30, 6))
    marker = store.get_sync(SOURCE, MODEL)
    await store.update(SOURCE, MODEL, marker.init, now=_utc(2026, 7, 30, 12))

    entries = {
        e.key: e for e in catalog.build(store=store, loop_interval_s=86400 * 36500)
    }
    assert entries[SOURCE].marker_health == "ok"
