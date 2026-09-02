"""Tests for the Météo-France TEMSI chart cache (AEROWEB)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from weatherbrief.fetch.meteofrance_charts import (
    AEROWEB_URL,
    CHART_IDS,
    CHART_NATIVE_SIZE,
    MAX_VALIDITY_GAP,
    AerowebError,
    _parse_cartes_xml,
    _pdf_to_png_bytes,
    build_route_overlay,
    chart_meta,
    chart_type_for,
    cycle_dir,
    discover_charts,
    enabled,
    evict_old_cycles,
    list_cycles,
    refresh_charts,
    resolve_chart_path,
    route_licence_allows,
    list_options_for_time,
)

_CODE = "TESTCODE00"


@pytest.fixture(autouse=True)
def _set_access_code(monkeypatch):
    monkeypatch.setenv("METEOFRANCE_API_CODE", _CODE)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pdf_with_image(width: int, height: int) -> bytes:
    """A one-page PDF wrapping a single raster — AEROWEB's envelope shape."""
    import fitz
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 10, 10)).save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())
    try:
        return doc.tobytes()
    finally:
        doc.close()


def _image_url(layer: str, stamp: str) -> str:
    return (
        "https://aviation.meteo.fr/FR/aviation/affiche_image.php"
        f"?login=TOKEN&layer={layer}&echeance={stamp}"
    )


_LAYERS = {"france": "sigwx/fr/france", "euroc": "sigwx/fr/teuroc"}


def _cartes_xml(zone: str, stamps: list[str], date_run: str | None = None) -> bytes:
    """A CARTES response for one zone, offering ``stamps`` validities."""
    cartes = []
    for stamp in stamps:
        run = date_run or f"{stamp[6:8]} {stamp[4:6]} {stamp[0:4]} {stamp[8:10]}:00"
        lien = (
            f"/FR/aviation/affiche_image.php?login=TOKEN"
            f"&layer={_LAYERS[zone]}&echeance={stamp}"
        )
        cartes.append(
            "<carte>"
            "<type>TEMSI</type>"
            "<niveau>FL20-150</niveau>"
            f"<zone_carte>{zone.upper()}</zone_carte>"
            f"<date_run>{run}</date_run>"
            f"<date_echeance>{run}</date_echeance>"
            f"<echeance>{stamp[8:10]} UTC</echeance>"
            f"<lien><![CDATA[{lien}]]></lien>"
            "</carte>"
        )
    body = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        f'<cartes><bloc_zone idz="{zone.upper()}" nom="{zone.upper()}">'
        + "".join(cartes)
        + "</bloc_zone></cartes>"
    )
    return body.encode("iso-8859-1")


def _register(stamps: list[str], date_run: str | None = None) -> None:
    """Register discovery + image responses for both zones."""
    for zone in CHART_IDS:
        responses.add(
            responses.GET,
            AEROWEB_URL,
            body=_cartes_xml(zone, stamps, date_run),
            content_type="text/xml",
            match=[responses.matchers.query_param_matcher(
                {
                    "ID": _CODE,
                    "TYPE_DONNEES": "CARTES",
                    "BASE_COMPLETE": "non",
                    "VUE_CARTE": "AERO_TEMSI",
                    "ZONE": "AERO_FRANCE" if zone == "france" else "AERO_EUROC",
                },
            )],
        )
        w, h = CHART_NATIVE_SIZE[zone]
        for stamp in stamps:
            # Both zones share the affiche_image.php path, so the layer +
            # echeance query must be matched or every chart gets the first
            # registered body (and thus the wrong zone's pixel size).
            responses.add(
                responses.GET,
                _image_url(_LAYERS[zone], stamp).split("?")[0],
                body=_pdf_with_image(w, h),
                content_type="application/pdf",
                match=[responses.matchers.query_param_matcher(
                    {"login": "TOKEN", "layer": _LAYERS[zone], "echeance": stamp},
                )],
            )


# ---------------------------------------------------------------------------
# PDF unwrapping
# ---------------------------------------------------------------------------


def test_pdf_to_png_extracts_native_raster():
    """The raster is extracted at source resolution, not the A4 page's."""
    from PIL import Image

    png = _pdf_to_png_bytes(_pdf_with_image(1160, 827))
    assert Image.open(io.BytesIO(png)).size == (1160, 827)


def test_pdf_to_png_rejects_non_pdf():
    with pytest.raises(Exception):
        _pdf_to_png_bytes(b"not a pdf at all")


# ---------------------------------------------------------------------------
# CARTES XML parsing
# ---------------------------------------------------------------------------


def test_parse_cartes_keys_by_valid_time():
    parsed = _parse_cartes_xml(_cartes_xml("france", ["20260831120000"]), "france")
    assert set(parsed) == {"2026-08-31T12Z"}
    entry = parsed["2026-08-31T12Z"]
    assert entry["zone"] == "france"
    assert entry["url"].startswith("https://aviation.meteo.fr/FR/aviation/affiche_image.php")
    assert entry["date_run"] == "31 08 2026 12:00"


def test_parse_cartes_rejects_bad_access_code():
    body = b'<?xml version="1.0"?><acces><code>NOK</code></acces>'
    with pytest.raises(AerowebError, match="access code"):
        _parse_cartes_xml(body, "france")


def test_parse_cartes_rejects_error_document():
    body = b"<?xml version='1.0'?><ERREUR><TYPE_DONNEES>CARTES</TYPE_DONNEES></ERREUR>"
    with pytest.raises(AerowebError):
        _parse_cartes_xml(body, "france")


def test_parse_cartes_skips_non_temsi():
    """A WINTEM in the same block must not be mistaken for a TEMSI."""
    body = _cartes_xml("france", ["20260831120000"]).replace(
        b"<type>TEMSI</type>", b"<type>WINTEM</type>",
    )
    assert _parse_cartes_xml(body, "france") == {}


def test_parse_cartes_skips_sub_hour_validity():
    """An unrepresentable validity is dropped, never truncated to the hour."""
    assert _parse_cartes_xml(_cartes_xml("france", ["20260831123000"]), "france") == {}


# ---------------------------------------------------------------------------
# Discovery + refresh
# ---------------------------------------------------------------------------


@responses.activate
def test_discover_merges_both_zones():
    _register(["20260831120000", "20260831150000"])
    found = discover_charts()
    assert sorted(found) == ["2026-08-31T12Z", "2026-08-31T15Z"]
    assert sorted(found["2026-08-31T12Z"]) == ["euroc", "france"]


@responses.activate
def test_discover_survives_one_bad_zone():
    """One zone failing must not starve the other."""
    responses.add(
        responses.GET, AEROWEB_URL,
        body=_cartes_xml("france", ["20260831120000"]), content_type="text/xml",
        match=[responses.matchers.query_param_matcher(
            {"ID": _CODE, "TYPE_DONNEES": "CARTES", "BASE_COMPLETE": "non",
             "VUE_CARTE": "AERO_TEMSI", "ZONE": "AERO_FRANCE"})],
    )
    responses.add(
        responses.GET, AEROWEB_URL, status=500,
        match=[responses.matchers.query_param_matcher(
            {"ID": _CODE, "TYPE_DONNEES": "CARTES", "BASE_COMPLETE": "non",
             "VUE_CARTE": "AERO_TEMSI", "ZONE": "AERO_EUROC"})],
    )
    found = discover_charts()
    assert list(found["2026-08-31T12Z"]) == ["france"]


@responses.activate
def test_refresh_writes_native_size_pngs(tmp_path: Path):
    from PIL import Image

    _register(["20260831120000", "20260831150000"])
    report = refresh_charts(tmp_path)

    assert report.charts_failed == []
    assert len(report.charts_refreshed) == 4
    assert list_cycles(tmp_path) == ["2026-08-31T12Z", "2026-08-31T15Z"]
    assert report.run_cycle == "2026-08-31T15Z"

    for cycle in list_cycles(tmp_path):
        for cid in CHART_IDS:
            path = resolve_chart_path(tmp_path, cycle, cid)
            assert path is not None and path.suffix == ".png"
            assert Image.open(path).size == CHART_NATIVE_SIZE[cid]


@responses.activate
def test_refresh_is_idempotent_via_date_run(tmp_path: Path):
    """Conditional GETs are inert against AEROWEB, so identity is date_run."""
    _register(["20260831120000"])
    refresh_charts(tmp_path)
    calls_after_first = len(responses.calls)

    second = refresh_charts(tmp_path)
    assert second.charts_refreshed == []
    assert sorted(second.charts_unchanged) == [
        "2026-08-31T12Z/euroc", "2026-08-31T12Z/france",
    ]
    # Only the two discovery queries were repeated — no image was re-fetched.
    assert len(responses.calls) - calls_after_first == 2


@responses.activate
def test_refresh_refetches_when_date_run_changes(tmp_path: Path):
    """A validity reissued from a later run must be picked up, not skipped."""
    _register(["20260831120000"], date_run="31 08 2026 06:00")
    refresh_charts(tmp_path)
    assert chart_meta(tmp_path, "2026-08-31T12Z", "france")["date_run"] == "31 08 2026 06:00"

    responses.reset()
    _register(["20260831120000"], date_run="31 08 2026 12:00")
    report = refresh_charts(tmp_path)
    assert sorted(report.charts_refreshed) == [
        "2026-08-31T12Z/euroc", "2026-08-31T12Z/france",
    ]
    assert chart_meta(tmp_path, "2026-08-31T12Z", "france")["date_run"] == "31 08 2026 12:00"


@responses.activate
def test_refresh_without_access_code_is_a_noop(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("METEOFRANCE_API_CODE", raising=False)
    assert enabled() is False
    report = refresh_charts(tmp_path)
    assert report.error == "METEOFRANCE_API_CODE not set"
    assert report.charts_refreshed == []
    assert len(responses.calls) == 0  # never touched the network


@responses.activate
def test_refresh_evicts_beyond_keep(tmp_path: Path):
    _register(["20260831060000", "20260831090000", "20260831120000"])
    report = refresh_charts(tmp_path, keep_cycles=2)
    assert report.evicted == ["2026-08-31T06Z"]
    assert list_cycles(tmp_path) == ["2026-08-31T09Z", "2026-08-31T12Z"]
    assert not cycle_dir(tmp_path, "2026-08-31T06Z").exists()


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@responses.activate
def test_options_are_ordered_nearest_first(tmp_path: Path):
    """The first entry is what the picker opens on, so order is load-bearing."""
    _register(["20260831120000", "20260831150000"])
    refresh_charts(tmp_path)

    def at(hour, minute=0):
        return datetime(2026, 8, 31, hour, minute, tzinfo=timezone.utc)

    # 12:10 — the 12Z pair is 10 min away, the 15Z pair 2h50. France leads each
    # pair: it is the low-level chart most GA flights are actually flown inside.
    assert list_options_for_time(tmp_path, at(12, 10)) == [
        ("france", "2026-08-31T12Z"), ("euroc", "2026-08-31T12Z"),
        ("france", "2026-08-31T15Z"), ("euroc", "2026-08-31T15Z"),
    ]
    assert list_options_for_time(tmp_path, at(14, 30))[0] == ("france", "2026-08-31T15Z")


@responses.activate
def test_no_options_beyond_the_horizon(tmp_path: Path):
    """A next-day flight gets no chart rather than a stale one."""
    _register(["20260831120000"])
    refresh_charts(tmp_path)
    tomorrow = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert list_options_for_time(tmp_path, tomorrow) == []


@responses.activate
def test_option_window_boundary(tmp_path: Path):
    _register(["20260831120000"])
    refresh_charts(tmp_path)
    valid = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    assert list_options_for_time(tmp_path, valid + MAX_VALIDITY_GAP) != []
    assert list_options_for_time(
        tmp_path, valid + MAX_VALIDITY_GAP + timedelta(minutes=1),
    ) == []


@responses.activate
def test_options_skip_zones_whose_bytes_are_gone(tmp_path: Path):
    """An option must never point at bytes that would 410."""
    _register(["20260831120000"])
    refresh_charts(tmp_path)
    resolve_chart_path(tmp_path, "2026-08-31T12Z", "euroc").unlink()
    at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    assert list_options_for_time(tmp_path, at) == [("france", "2026-08-31T12Z")]


# ---------------------------------------------------------------------------
# Licence gate
# ---------------------------------------------------------------------------


def _route(*points):
    from weatherbrief.models.analysis import RouteConfig, Waypoint

    return RouteConfig(
        name="t",
        waypoints=[Waypoint(icao=i, name=i, lat=la, lon=lo) for i, la, lo in points],
    )


def test_licence_allows_french_route():
    assert route_licence_allows(_route(
        ("LFPG", 49.010, 2.548), ("LFMN", 43.665, 7.215),
    )) is True


def test_licence_allows_overflight_without_french_airport():
    """The gate is airspace, not aerodromes: LSGG->EGKK crosses France."""
    assert route_licence_allows(_route(
        ("LSGG", 46.238, 6.109), ("EGKK", 51.148, -0.190),
    )) is True


def test_licence_denies_route_outside_france():
    assert route_licence_allows(_route(
        ("EGLL", 51.470, -0.454), ("EGCC", 53.354, -2.275),
    )) is False


def test_licence_denies_missing_route():
    """Fail closed: no route means no proof of entitlement."""
    assert route_licence_allows(None) is False


def test_licence_denies_when_detection_unavailable(monkeypatch):
    """A degraded environment must not hand out licensed bytes."""
    import weatherbrief.airports as airports

    def boom(*a, **k):
        raise RuntimeError("timezone data unavailable")

    monkeypatch.setattr(airports, "route_countries", boom)
    assert route_licence_allows(_route(
        ("LFPG", 49.010, 2.548), ("LFMN", 43.665, 7.215),
    )) is False


# ---------------------------------------------------------------------------
# Calibration state
# ---------------------------------------------------------------------------


def test_chart_type_is_the_zone():
    assert [chart_type_for(c) for c in CHART_IDS] == list(CHART_IDS)


def test_route_overlay_covers_both_calibrated_zones():
    overlay = build_route_overlay([("LFPG", 49.010, 2.548), ("LFML", 43.438, 5.213)])
    assert set(overlay) == set(CHART_IDS)
    for zone, entry in overlay.items():
        assert entry["native_size"] == list(CHART_NATIVE_SIZE[zone])
        w, h = CHART_NATIVE_SIZE[zone]
        for wp in entry["waypoints"]:
            assert 0 <= wp["x"] < w and 0 <= wp["y"] < h


def test_projection_matches_control_points():
    """Guards the calibration constants against an accidental edit.

    These are the clicked graticule crossings the homographies were fit from
    (max residual 0.43 px france / 0.99 px euroc), so a 2 px tolerance catches
    a swapped or corrupted coefficient without being brittle.
    """
    from weatherbrief.fetch.meteofrance_charts import lonlat_to_chart_pixel

    control = {
        "france": [(-5, 45, 153, 405), (0, 45, 390, 416), (5, 45, 627, 405),
                   (-5, 50, 182, 78), (0, 50, 390, 87), (5, 50, 598, 78)],
        "euroc": [(-5, 45, 199, 669), (0, 45, 315, 675), (5, 45, 432, 669),
                  (-5, 50, 215, 508), (0, 50, 315, 514), (5, 50, 417, 509)],
    }
    for zone, points in control.items():
        for lon, lat, x, y in points:
            px, py = lonlat_to_chart_pixel(lon, lat, zone)
            assert abs(px - x) <= 2 and abs(py - y) <= 2, (
                f"{zone} {lat}N {lon}E: got ({px},{py}) want ({x},{y})"
            )


def test_uncalibrated_zone_would_be_omitted(monkeypatch):
    """The safety property still holds if a zone is ever added uncalibrated."""
    from weatherbrief.fetch import meteofrance_charts as mod

    monkeypatch.setattr(mod._cache.calibrations["euroc"], "homography", None)
    overlay = build_route_overlay([("LFPG", 49.010, 2.548)])
    assert set(overlay) == {"france"}


def test_every_chart_id_has_a_native_size():
    assert set(CHART_NATIVE_SIZE) == set(CHART_IDS)
