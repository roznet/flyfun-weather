# Fetch Layer

> Weather data retrieval: Open-Meteo multi-model, DWD text forecasts, Autorouter GRAMET

## Intent

Centralize all external data fetching with graceful failure handling. Each data source may be unavailable; the pipeline continues with whatever succeeds.

## Open-Meteo Client (`fetch/open_meteo.py`)

Multi-model NWP data from the free Open-Meteo API.

### Model Endpoints (`fetch/variables.py`)

Each model has a `ModelEndpoint` dataclass specifying URL, max forecast range, and unavailable variables:

| Model | Endpoint | Max Days | Notes |
|-------|----------|----------|-------|
| `best_match` | `/v1/forecast` | 16 | Open-Meteo's auto-blend |
| `gfs` | `/v1/gfs` | 16 | NCEP GFS |
| `ecmwf` | `/v1/ecmwf` | 10 | No surface dewpoint |
| `icon` | `/v1/dwd-icon` | 7 | No precip probability |
| `ukmo` | `/v1/forecast?models=ukmo_seamless` | 7 | Uses `model_param` field |
| `meteofrance` | `/v1/meteofrance` | 6 | No surface dewpoint |

`build_hourly_params()` constructs the API parameter string, excluding each model's unavailable variables. Pressure levels: `[1000, 925, 850, 700, 600, 500, 400, 300]` hPa.

### Client Usage

```python
client = OpenMeteoClient()
# Single model
forecast = client.fetch_forecast(waypoint, ModelSource.GFS)
# All models (skips out-of-range)
forecasts = client.fetch_all_models(waypoint, models, days_out=7)
```

### Key Choices

- **Wind in knots** — `wind_speed_unit=kn` for aviation
- **Magnus dewpoint derivation** — when API doesn't provide dewpoint at pressure levels, derived from T + RH using `magnus_dewpoint(temp_c, rh_pct)` (b=17.67, c=243.5)
- **Range filtering** — `fetch_all_models` skips models where `days_out >= max_days`
- **Graceful failure** — individual model failures logged, others continue
- **UKMO model_param** — uses generic `/v1/forecast` with `?models=ukmo_seamless` query param

## DWD Text Forecasts (`fetch/dwd_text.py`)

German synoptic overviews from DWD Open Data (free, no API key).

```python
text_fcsts = fetch_dwd_text_forecasts()
text_fcsts.short_range   # SXDL31 Kurzfrist (2-3 day), updated 2x daily
text_fcsts.medium_range  # SXDL33 Mittelfrist (7-day), updated daily ~10:30 UTC
```

- **latin-1 encoding** — DWD files use ISO 8859-1, not UTF-8
- **Graceful failure** — each forecast independently None-able; catches both `RequestException` and `ConnectionError`
- Text is in German — the LLM translates and synthesizes as part of the digest

## Autorouter GRAMET (`fetch/gramet.py`)

Vertical cross-section images from the Autorouter API (requires euro_aip credentials).

```python
gramet = AutorouterGramet()  # uses AutorouterCredentialManager
data = gramet.fetch_gramet(
    icao_codes=["EGTK", "LFPB", "LSGS"],
    altitude_ft=8000,
    departure_time=dt,
    duration_hours=4.5,
)  # → bytes (PNG)
```

- Auth via Bearer token from `euro_aip.utils.autorouter_credentials`
- API params: waypoints (space-separated), altitude (feet), departuretime (Unix), totaleet (seconds)

## Gotchas

- ECMWF now has relative_humidity at pressure levels (dewpoint still derived via Magnus)
- Open-Meteo API returns flat arrays keyed by variable name, indexed by time step
- DWD URLs use `_LATEST` suffix for most recent version of each forecast type

## References

- Variable definitions: `fetch/variables.py`
- Data models: [data-models.md](./data-models.md)
- Analysis consumers: [analysis.md](./analysis.md)
