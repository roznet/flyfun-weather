"""Tests for the unified help-catalog endpoint (issue #311).

The endpoint merges the English metrics catalog (web/ts/data/metrics-catalog.json)
with the advisory catalog (get_catalog()) into one versioned, ETag-cacheable
payload that the iOS app caches and renders (i) popups from offline.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    help_module._route_map_metrics_cache = None
    yield
    help_module._payload_cache.clear()
    help_module._metrics_cache = None
    help_module._map_metrics_cache = None
    help_module._route_map_metrics_cache = None


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

    def test_payload_carries_the_route_map_catalog(self):
        # The briefing route-map metrics (#998) are served under `route_maps`
        # so the apps list the same metrics, groups, legends and default.
        payload = json.loads(help_module._build_payload("en")[0])
        route = payload["route_maps"]
        keys = [m["key"] for m in route["metrics"]]
        assert route["default_metric"] in keys
        assert "icing-risk-at-level" in keys and "wind-speed" in keys
        # Width is gone (#998): line width follows the colour metric.
        assert all("width" in m for m in route["metrics"])

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


ROOT = Path(__file__).resolve().parents[1]
ROUTE_CATALOG = ROOT / "web/ts/data/route-map-metrics-catalog.json"
BUNDLED_COPIES = {
    "map-metrics-catalog.json": [
        ROOT / "app/flyfun-weather/flyfun-weather/Resources/map-metrics-catalog.json",
        ROOT / "app/android/app/src/main/assets/map-metrics-catalog.json",
    ],
    "route-map-metrics-catalog.json": [
        ROOT / "app/flyfun-weather/flyfun-weather/Resources/route-map-metrics-catalog.json",
        ROOT / "app/android/app/src/main/assets/route-map-metrics-catalog.json",
    ],
}


class TestRouteMapCatalog:
    """The briefing route-map catalog (#998) is well formed and bundled verbatim."""

    @pytest.fixture
    def catalog(self) -> dict:
        return json.loads(ROUTE_CATALOG.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("name", sorted(BUNDLED_COPIES))
    def test_bundled_copies_match_the_web_source(self, name):
        source = (ROOT / "web/ts/data" / name).read_bytes()
        for copy in BUNDLED_COPIES[name]:
            assert copy.read_bytes() == source, f"{copy} is stale: copy web/ts/data/{name} over it"

    def test_keys_unique_and_default_listed(self, catalog):
        keys = [m["key"] for m in catalog["metrics"]]
        assert len(keys) == len(set(keys))
        assert catalog["default_metric"] in keys

    def test_every_metric_is_complete(self, catalog):
        groups = {g["key"] for g in catalog["groups"]}
        bands = catalog["scales"]["bands"]
        for m in catalog["metrics"]:
            key = m["key"]
            assert m["group"] in groups, key
            assert m["scale"] in bands, f"{key} -> unknown scale {m['scale']}"
            assert m["aggregation"] in {"average", "max"}, key
            assert isinstance(m["uses_altitude"], bool), key
            assert m["value"], key
            assert m["label"] and m["i18n_key"], key
            assert m["width"]["thin_at"] != m["width"]["thick_at"], key
            assert m["legend"], key
            assert m["format"]["kind"] in {"number", "rank"}, key
            # Altitude metrics live in the "At altitude" group, the rest along the route.
            assert (m["group"] == "at_altitude") == m["uses_altitude"], key

    def test_scales_use_the_shared_vocabulary(self, catalog):
        for name, scale in catalog["scales"]["bands"].items():
            kind = scale["kind"]
            assert kind in {"threshold_asc", "threshold_desc", "gray_ramp", "linear"}, name
            if kind == "linear":
                ats = [s["at"] for s in scale["stops"]]
                assert ats == sorted(ats) and len(ats) >= 2, name
                assert all(s["color"].startswith("#") and len(s["color"]) == 7 for s in scale["stops"]), name
            elif kind.startswith("threshold"):
                assert scale["stops"] and scale["default"], name

    def test_i18n_keys_exist_in_every_web_locale(self, catalog):
        keys = {catalog_key for catalog_key in _catalog_i18n_keys(catalog)}
        for lang in ("en", "fr", "de", "es"):
            flat = _flatten(json.loads((ROOT / f"web/ts/i18n/locales/{lang}.json").read_text(encoding="utf-8")))
            missing = sorted(k for k in keys if k not in flat)
            assert not missing, f"{lang}.json lacks {missing}"


def _catalog_i18n_keys(catalog: dict):
    for g in catalog["groups"]:
        yield g["i18n_key"]
    for m in catalog["metrics"]:
        yield m["i18n_key"]
        for item in m["legend"]:
            if "i18n_key" in item:
                yield item["i18n_key"]


def _flatten(obj: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out
