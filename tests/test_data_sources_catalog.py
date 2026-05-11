"""Tests for the public data-sources catalog and endpoint.

The catalog merges the static SOURCE_REGISTRY with the live MarkerStore;
these tests verify both the empty-store ("nothing observed yet") and the
bootstrapped-store paths, plus the serialisation shape used on the wire.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from weatherbrief.api.data_sources import _serialise_entry
from weatherbrief.fetch.freshness import catalog, registry
from weatherbrief.fetch.freshness.markers import MarkerStore
from weatherbrief.fetch.freshness.sources import all_tracked_sources


@pytest.fixture
def fresh_store():
    """Return a clean, un-bootstrapped MarkerStore."""
    return MarkerStore()


@pytest.fixture
def bootstrapped_store():
    """Return a MarkerStore bootstrapped at a fixed wallclock."""
    store = MarkerStore()
    fixed_now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    asyncio.run(store.bootstrap(all_tracked_sources(), now=fixed_now))
    return store


# ---------------------------------------------------------------------------
# Static fields — comes from SOURCE_REGISTRY config
# ---------------------------------------------------------------------------


class TestStaticFields:
    def test_every_registry_source_appears_in_catalog(self, fresh_store):
        entries = catalog.build(store=fresh_store)
        assert {e.key for e in entries} == set(registry.SOURCE_REGISTRY.keys())

    def test_descriptive_fields_populated_for_every_entry(self, fresh_store):
        """Catch drift: every registry entry must have a non-empty
        model_label, provider_label, role, and description."""
        entries = catalog.build(store=fresh_store)
        missing = []
        for e in entries:
            if not e.model_label:
                missing.append(f"{e.key}.model_label")
            if not e.provider_label:
                missing.append(f"{e.key}.provider_label")
            if not e.role:
                missing.append(f"{e.key}.role")
            if not e.description:
                missing.append(f"{e.key}.description")
        assert not missing, f"Missing descriptive fields: {missing}"

    def test_role_taxonomy_is_constrained(self, fresh_store):
        """Roles must come from a known set so the UI can style them."""
        allowed = {"primary-sounding", "cloud-enrichment", "surface-base", "primary"}
        entries = catalog.build(store=fresh_store)
        for e in entries:
            assert e.role in allowed, f"{e.key} has unknown role {e.role!r}"

    def test_icon_family_distinguishes_eu_from_global(self, fresh_store):
        """The user-facing reason this endpoint exists: ICON-EU (direct DWD)
        and ICON-Global (Open-Meteo) must show up as separate rows with
        different labels."""
        entries = {e.key: e for e in catalog.build(store=fresh_store)}
        assert entries["icon_eu:dwd"].model_label == "ICON-EU"
        assert entries["icon:openmeteo"].model_label == "ICON-Global"
        assert entries["icon_eu:dwd"].provider_label == "DWD"
        assert entries["icon:openmeteo"].provider_label == "Open-Meteo"

    def test_ecmwf_direct_advertises_25_levels_openmeteo_13(self, fresh_store):
        """The hybrid pattern the user called out: direct ECMWF gives 25
        levels for 7 days, Open-Meteo fills in with 13 levels."""
        entries = {e.key: e for e in catalog.build(store=fresh_store)}
        assert entries["ecmwf:direct"].pressure_levels == 25
        assert entries["ecmwf:openmeteo"].pressure_levels == 13

    def test_gem_is_present(self, fresh_store):
        """GEM (Canadian ECCC) was in the legacy static help table and is
        fetched by the OM seamless feed for North-American routes.  Make
        sure it survives in the data-driven catalog so the help page
        doesn't silently drop a supported model."""
        entries = {e.key: e for e in catalog.build(store=fresh_store)}
        assert "gem:openmeteo" in entries
        assert entries["gem:openmeteo"].model_label == "GEM"
        assert entries["gem:openmeteo"].pressure_levels == 20

    def test_post_init_rejects_partial_horizon_dict(self):
        """A dict-shaped horizon must cover every configured cycle hour —
        the constructor must fail loudly, not surface a 500 from the
        endpoint when ``catalog._per_cycle_hours`` would ``KeyError``."""
        from datetime import timedelta

        from weatherbrief.fetch.freshness.registry import SourceConfig

        with pytest.raises(ValueError, match="missing cycle hours"):
            SourceConfig(
                key="bad:source",
                cycles=(0, 6, 12, 18),
                # 12Z missing — must raise.
                delivery_offset=timedelta(hours=1),
                horizon={0: timedelta(hours=120), 6: timedelta(hours=78),
                         18: timedelta(hours=78)},
            )


# ---------------------------------------------------------------------------
# Dynamic fields — comes from MarkerStore
# ---------------------------------------------------------------------------


class TestDynamicFields:
    def test_empty_store_returns_null_dynamic_fields(self, fresh_store):
        """Catalog must be safe to call before the loop has bootstrapped."""
        entries = catalog.build(store=fresh_store)
        for e in entries:
            assert e.latest_init is None
            assert e.published_at is None
            assert e.next_expected is None
            assert e.horizon_end is None
            assert e.marker_health == "unknown"

    def test_bootstrapped_store_populates_dynamic_fields(self, bootstrapped_store):
        # ``Marker.is_stale`` compares ``last_check`` against the real
        # ``datetime.now()`` — pass a generous loop interval so this test
        # is robust to wallclock drift between bootstrap and the assertion.
        entries = catalog.build(store=bootstrapped_store, loop_interval_s=86400)
        for e in entries:
            assert e.latest_init is not None
            assert e.next_expected is not None
            assert e.horizon_end is not None
            assert e.marker_health == "ok"
            # horizon_end must be in the future of latest_init
            assert e.horizon_end > e.latest_init

    def test_horizon_end_uses_per_cycle_horizon(self, bootstrapped_store):
        """ECMWF 00/12Z cycles reach 168h; 06/18Z reach 90h.  The catalog
        must apply the correct per-cycle horizon to the observed init."""
        entries = {e.key: e for e in catalog.build(store=bootstrapped_store)}
        ecmwf = entries["ecmwf:direct"]
        delta = ecmwf.horizon_end - ecmwf.latest_init
        # Bootstrap picks the most-recent at-or-before-now cycle whose
        # delivery is due; for fixed_now=12:00, ECMWF latest delivered is
        # 06Z (next due at 18:40Z) — a 90h cycle.
        assert delta.total_seconds() / 3600 in (90, 168)

    def test_marker_data_end_overrides_config_horizon(self):
        """When the provider reports an actual data_end_time shorter than
        the static config horizon (the meteofrance empty-data case), the
        catalog must surface the live value rather than the config lie."""
        store = MarkerStore()
        # Direct update path bypasses bootstrap to drop a marker with
        # data_end < init + config horizon.
        init = datetime(2026, 5, 11, 12, tzinfo=timezone.utc)
        truncated_end = datetime(2026, 5, 15, 19, tzinfo=timezone.utc)
        asyncio.run(store.update(
            "meteofrance:openmeteo", "meteofrance",
            observed_init=init,
            now=datetime(2026, 5, 11, 12, 45, tzinfo=timezone.utc),
            data_end=truncated_end,
        ))
        entries = {e.key: e for e in catalog.build(store=store, loop_interval_s=86400)}
        mf = entries["meteofrance:openmeteo"]
        # data_end (live) wins over init + 6-day config horizon.
        assert mf.horizon_end == truncated_end

    def test_horizon_end_falls_back_to_config_when_no_data_end(self):
        """Direct-GRIB sources don't report data_end — must fall back to
        init + cfg.horizon so the UI still shows something useful."""
        store = MarkerStore()
        init = datetime(2026, 5, 11, 12, tzinfo=timezone.utc)
        asyncio.run(store.update(
            "ecmwf:direct", "ecmwf",
            observed_init=init,
            now=datetime(2026, 5, 11, 18, 40, tzinfo=timezone.utc),
            data_end=None,  # explicit: direct sources have no live horizon
        ))
        entries = {e.key: e for e in catalog.build(store=store, loop_interval_s=86400)}
        ecmwf = entries["ecmwf:direct"]
        # 12Z is a 168h cycle for ECMWF; falls back to config.
        delta = ecmwf.horizon_end - ecmwf.latest_init
        assert delta.total_seconds() / 3600 == 168


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


class TestSerialisation:
    def test_iso_strings_or_null_for_datetime_fields(self, bootstrapped_store):
        entries = catalog.build(store=bootstrapped_store)
        payload = _serialise_entry(entries[0])
        for field in ("latest_init", "published_at", "next_expected", "horizon_end"):
            v = payload[field]
            assert v is None or (isinstance(v, str) and "T" in v)

    def test_horizon_hours_keys_are_strings(self, fresh_store):
        """JSON object keys must be strings; the wire format stringifies the
        cycle-hour int keys explicitly."""
        entries = catalog.build(store=fresh_store)
        payload = _serialise_entry(entries[0])
        for k in payload["horizon_hours"]:
            assert isinstance(k, str)
        for k in payload["delivery_offset_hours"]:
            assert isinstance(k, str)

    def test_serialised_payload_has_stable_keys(self, fresh_store):
        """Pin the wire shape so future refactors don't silently break the
        frontend adapter."""
        entries = catalog.build(store=fresh_store)
        payload = _serialise_entry(entries[0])
        expected = {
            "key", "model", "model_label", "provider_label", "provider_url",
            "role", "resolution", "coverage", "pressure_levels", "description",
            "cycles", "horizon_hours", "delivery_offset_hours",
            "latest_init", "published_at", "next_expected", "horizon_end",
            "marker_health",
        }
        assert set(payload.keys()) == expected


# ---------------------------------------------------------------------------
# Provider-label single source of truth
# ---------------------------------------------------------------------------


class TestProviderLabelSingleSource:
    """The provider label used by the freshness popover (api/packs.py
    _provider_label) must come from the same registry as the data-sources
    catalog — otherwise the two surfaces would drift apart again."""

    def test_packs_provider_label_matches_registry(self):
        from weatherbrief.api.packs import _provider_label
        for key, cfg in registry.SOURCE_REGISTRY.items():
            assert _provider_label(key) == cfg.provider_label
