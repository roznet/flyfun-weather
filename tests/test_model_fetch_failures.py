"""A model whose fetch fails is retried, then reported as unavailable (#835)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.model_agreement import ModelAgreementEvaluator
from weatherbrief.digest.prompt_builder import _models_header_line
from weatherbrief.fetch.open_meteo import OpenMeteoClient
from weatherbrief.models import (
    AgreementLevel,
    FetchCode,
    ModelDivergence,
    RouteAdvisoriesManifest,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _models_failed_for_ctx
from weatherbrief.tasks.artifacts import write_pack_meta
from weatherbrief.tasks.fetch import _build_fetch_diagnostics, model_failure_reason


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    return resp


def _client_with(side_effect) -> OpenMeteoClient:
    client = OpenMeteoClient()
    client.session = MagicMock()
    client.session.get.side_effect = side_effect
    return client


# --- A1: retry on timeout / connection error ---


@patch("weatherbrief.fetch.open_meteo.time.sleep")
def test_timeout_once_then_ok_succeeds(mock_sleep):
    ok = _ok_response()
    client = _client_with([requests.exceptions.ReadTimeout("read timed out"), ok])
    assert client._get_with_retry("https://api.open-meteo.com/v1/ecmwf", {}) is ok
    assert client.session.get.call_count == 2
    assert client.call_count == 2
    mock_sleep.assert_called_once_with(5)


@patch("weatherbrief.fetch.open_meteo.time.sleep")
def test_connection_error_is_retried(mock_sleep):
    ok = _ok_response()
    client = _client_with([requests.exceptions.ConnectionError("reset"), ok])
    assert client._get_with_retry("https://api.open-meteo.com/v1/ecmwf", {}) is ok


@patch("weatherbrief.fetch.open_meteo.time.sleep")
def test_timeout_every_attempt_raises_after_three_calls(mock_sleep):
    client = _client_with(requests.exceptions.ReadTimeout("read timed out"))
    with pytest.raises(requests.exceptions.ReadTimeout):
        client._get_with_retry("https://api.open-meteo.com/v1/ecmwf", {})
    assert client.session.get.call_count == 3
    assert [c.args[0] for c in mock_sleep.call_args_list] == [5, 15]


@patch("weatherbrief.fetch.open_meteo.time.sleep")
def test_http_4xx_is_not_retried(mock_sleep):
    resp = MagicMock()
    resp.status_code = 400
    resp.raise_for_status.side_effect = requests.HTTPError("400", response=resp)
    client = _client_with([resp])
    with pytest.raises(requests.HTTPError):
        client._get_with_retry("https://api.open-meteo.com/v1/ecmwf", {})
    assert client.session.get.call_count == 1
    mock_sleep.assert_not_called()


# --- A2: structured per-model status ---


@pytest.mark.parametrize("exc, reason", [
    (requests.exceptions.ReadTimeout("x"), "timeout"),
    (requests.exceptions.ConnectTimeout("x"), "timeout"),
    (requests.exceptions.ConnectionError("x"), "connection"),
    (requests.HTTPError("x", response=MagicMock(status_code=400)), "http_400"),
    (ValueError("bad json"), "error"),
])
def test_model_failure_reason(exc, reason):
    assert model_failure_reason(exc) == reason


def _diags(models_failed=None, models_fetched=("gfs",)):
    return _build_fetch_diagnostics(
        requested_models=["gfs", "ecmwf"],
        models_fetched=list(models_fetched),
        models_skipped_region=[],
        models_failed=models_failed,
        enrich_grib=False,
        grib_enriched=False,
        grib_enrichment_failed=False,
        grib_init_times={},
    )


def test_fetch_failed_diagnostic_carries_reason():
    failed = [d for d in _diags({"ecmwf": "timeout"}) if d.code == FetchCode.MODEL_FETCH_FAILED]
    assert len(failed) == 1
    assert failed[0].level == "warn"
    assert failed[0].message == "ECMWF forecast fetch failed (timeout)"


def test_fetch_failed_diagnostic_fallback_without_record():
    failed = [d for d in _diags(None) if d.code == FetchCode.MODEL_FETCH_FAILED]
    assert [d.message for d in failed] == ["ECMWF forecast fetch failed"]


def test_pack_meta_round_trips_models_failed(tmp_path):
    write_pack_meta(tmp_path, models_fetched=["gfs"], diagnostics=[],
                    models_failed={"ecmwf": "timeout"})
    meta = json.loads((tmp_path / "fetch_meta.json").read_text())
    assert meta["models_failed"] == {"ecmwf": "timeout"}
    assert _models_failed_for_ctx(tmp_path, None) == {"ecmwf": "timeout"}
    # A model the user excluded from advisories is not reported missing.
    assert _models_failed_for_ctx(tmp_path, ["gfs"]) == {}


def test_old_pack_without_field_reads_empty(tmp_path):
    (tmp_path / "fetch_meta.json").write_text(json.dumps({
        "fetched_at": "2026-10-18T06:00:00+00:00",
        "models_fetched": ["gfs", "ecmwf"],
        "diagnostics": [],
    }))
    assert _models_failed_for_ctx(tmp_path, None) == {}
    assert _models_failed_for_ctx(None, None) == {}
    assert RouteAdvisoriesManifest.model_validate({}).models_failed == {}


# --- A3: consumers ---


def _agreement_ctx(models, models_failed):
    def _good():
        return [ModelDivergence(
            variable=v, model_values={m: 5.0 for m in models},
            mean=5.0, spread=0.0, agreement=AgreementLevel.GOOD,
        ) for v in ("temperature_c", "wind_speed_kt")]

    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={m: SoundingAnalysis() for m in models},
            model_divergence=_good(),
        )
        for i in range(10)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None,
        models=models, cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=200, models_failed=models_failed,
    )


_PARAMS = {"min_poor_vars": 3, "extent_pct_amber": 25, "extent_pct_red": 50}


def test_agreement_names_missing_model():
    ctx = _agreement_ctx(["gfs", "icon"], {"ecmwf": "timeout"})
    result = ModelAgreementEvaluator.evaluate(ctx, _PARAMS)
    detail = result.per_model[0].detail
    assert "2 of 3 models" in detail
    assert "ECMWF unavailable" in detail
    assert "all models" not in detail


def test_agreement_unchanged_when_nothing_failed():
    ctx = _agreement_ctx(["gfs", "icon"], {})
    detail = ModelAgreementEvaluator.evaluate(ctx, _PARAMS).per_model[0].detail
    assert detail == "Good agreement across all models"


def test_agreement_grade_is_unchanged_by_failure():
    a = ModelAgreementEvaluator.evaluate(_agreement_ctx(["gfs", "icon"], {}), _PARAMS)
    b = ModelAgreementEvaluator.evaluate(
        _agreement_ctx(["gfs", "icon"], {"ecmwf": "timeout"}), _PARAMS,
    )
    assert a.aggregate_status == b.aggregate_status


def test_digest_models_line():
    manifest = RouteAdvisoriesManifest(models=["gfs", "icon"], models_failed={"ecmwf": "timeout"})
    line = _models_header_line(manifest)
    assert line.startswith("\nMODELS: GFS, ICON.")
    assert "UNAVAILABLE: ECMWF (timeout)" in line


def test_digest_models_line_empty_when_all_arrived():
    assert _models_header_line(RouteAdvisoriesManifest(models=["gfs", "ecmwf"])) == ""
    assert _models_header_line(None) == ""
