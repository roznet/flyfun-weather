"""Tests for the unified help-catalog endpoint (issue #311).

The endpoint merges the English metrics catalog (web/ts/data/metrics-catalog.json)
with the advisory catalog (get_catalog()) into one versioned, ETag-cacheable
payload that the iOS app caches and renders (i) popups from offline.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from weatherbrief.api import help as help_module
from weatherbrief.api.app import create_app


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset the module-level memoization between tests."""
    help_module._payload_cache.clear()
    help_module._metrics_cache = None
    help_module._map_metrics_cache = None
    yield
    help_module._payload_cache.clear()
    help_module._metrics_cache = None
    help_module._map_metrics_cache = None


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPayloadBuilding:
    def test_metrics_catalog_loads_with_entries(self):
        metrics = help_module._load_metrics_catalog()
        assert isinstance(metrics, dict)
        assert len(metrics) > 0
        # CAPE is a known, stable entry — sanity-check the shape.
        assert "cape_surface_jkg" in metrics
        assert metrics["cape_surface_jkg"]["name"] == "CAPE"

    def test_payload_has_version_metrics_and_advisories(self):
        body, version = help_module._build_payload("en")
        payload = json.loads(body)
        assert payload["version"] == version
        assert len(version) == 16
        assert isinstance(payload["metrics"], dict) and payload["metrics"]
        assert isinstance(payload["advisories"], list) and payload["advisories"]
        # Advisory entry shape mirrors AdvisoryCatalogEntry.
        entry = payload["advisories"][0]
        assert {"id", "name", "short_description", "description", "category"} <= set(entry)

    def test_payload_carries_the_forecast_map_catalog(self):
        # The forecast-map colour/threshold catalog is served here (B2, #419) so
        # iOS reads the same scales the web bundle imports.
        payload = json.loads(help_module._build_payload("en")[0])
        maps = payload["maps"]
        assert maps["scales"]["categorical"]["flight_category"]["VFR"] == "#22c55e"
        assert "wind_speed_kt" in maps["scales"]["bands"]
        assert maps["metrics"]["flight_category"]["label"] == "Category"

    def test_payload_carries_the_debrief_taxonomy(self):
        # The debrief taxonomy is served here so iOS renders the debrief form
        # from the same vocabulary the web bundle imports at build time.
        payload = json.loads(help_module._build_payload("en")[0])
        debrief = payload["debrief"]
        # Decisions in display order, Flown first.
        assert [d["id"] for d in debrief["decisions"]] == ["flown", "cancelled", "monitoring"]
        # Every ConditionTag present; OPS is not an outcome category, IMC is.
        tags = {t["id"]: t for t in debrief["tags"]}
        assert set(tags) == {"IMC", "ICE", "WIND", "TS", "TURB", "FRZ", "VIS", "OPS"}
        assert tags["OPS"]["outcome_category"] is False
        assert tags["ICE"]["outcome_category"] is True
        assert tags["ICE"]["label"] == "Icing"
        assert [v["id"] for v in debrief["outcome_values"]] == ["consistent", "better", "worse"]
        assert debrief["advisory_tag_map"]["convective"] == "TS"
        assert debrief["note_max_length"] == 300

    def test_debrief_taxonomy_folded_into_version(self):
        # A taxonomy edit must re-validate the ETag (it's part of the hash).
        import weatherbrief.debriefs.taxonomy as tax

        _, v1 = help_module._build_payload("en")
        original = tax.TAG_LABELS[tax.ConditionTag.ICE]
        tax.TAG_LABELS[tax.ConditionTag.ICE] = "Rime & clear ice"
        try:
            help_module._payload_cache.clear()
            _, v2 = help_module._build_payload("en")
        finally:
            tax.TAG_LABELS[tax.ConditionTag.ICE] = original
        assert v1 != v2

    def test_version_is_stable(self):
        _, v1 = help_module._build_payload("en")
        help_module._payload_cache.clear()
        _, v2 = help_module._build_payload("en")
        assert v1 == v2

    def test_metrics_english_regardless_of_lang(self):
        body_en, _ = help_module._build_payload("en")
        body_fr, _ = help_module._build_payload("fr")
        # Metrics are English-only — identical content under any language.
        assert json.loads(body_en)["metrics"] == json.loads(body_fr)["metrics"]

    def test_lang_changes_version_for_cache_busting(self):
        # lang is folded into the hash so a client switching language re-fetches,
        # even while content is still English-only.
        _, v_en = help_module._build_payload("en")
        _, v_fr = help_module._build_payload("fr")
        assert v_en != v_fr


class TestEtagMatching:
    def test_no_header_never_matches(self):
        assert help_module._etag_matches(None, "abc") is False

    def test_exact_quoted_match(self):
        assert help_module._etag_matches('"abc"', "abc") is True

    def test_weak_prefix_match(self):
        assert help_module._etag_matches('W/"abc"', "abc") is True

    def test_wildcard_match(self):
        assert help_module._etag_matches("*", "abc") is True

    def test_list_match(self):
        assert help_module._etag_matches('"x", "abc"', "abc") is True

    def test_mismatch(self):
        assert help_module._etag_matches('"stale"', "abc") is False


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


class TestEndpoint:
    def test_200_returns_merged_catalog_with_etag(self, client):
        resp = client.get("/api/help/catalog")
        assert resp.status_code == 200
        assert resp.headers["ETag"]
        data = resp.json()
        assert data["version"]
        assert data["metrics"]["cape_surface_jkg"]["name"] == "CAPE"
        assert any(a["id"] == "convective" for a in data["advisories"])
        # Debrief taxonomy rides in the same payload.
        assert [d["id"] for d in data["debrief"]["decisions"]] == ["flown", "cancelled", "monitoring"]
        # ETag wraps the version exactly.
        assert resp.headers["ETag"] == f'"{data["version"]}"'

    def test_matching_if_none_match_returns_304(self, client):
        first = client.get("/api/help/catalog")
        etag = first.headers["ETag"]
        second = client.get("/api/help/catalog", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.headers["ETag"] == etag
        assert second.content == b""

    def test_stale_if_none_match_returns_200(self, client):
        resp = client.get(
            "/api/help/catalog", headers={"If-None-Match": '"deadbeefdeadbeef"'}
        )
        assert resp.status_code == 200
        assert resp.json()["metrics"]

    def test_lang_param_accepted_metrics_still_english(self, client):
        resp = client.get("/api/help/catalog", params={"lang": "fr"})
        assert resp.status_code == 200
        # Metrics returned in English regardless of lang.
        assert resp.json()["metrics"]["cape_surface_jkg"]["name"] == "CAPE"

    def test_unknown_lang_falls_back_to_english_etag(self, client):
        en = client.get("/api/help/catalog", params={"lang": "en"})
        bogus = client.get("/api/help/catalog", params={"lang": "zz"})
        assert bogus.status_code == 200
        # Unknown lang normalizes to en → same version/ETag.
        assert bogus.headers["ETag"] == en.headers["ETag"]
