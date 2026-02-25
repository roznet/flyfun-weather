---
name: audit-sources
description: Audit Open-Meteo and other weather data sources for newly available variables, pressure levels, and models
---

# Audit Weather Data Sources

Systematically probe all configured weather data sources to detect newly available variables, pressure levels, or models that we're not yet using. Open-Meteo expands its offerings as upstream providers (ECMWF, DWD, NCEP, etc.) open more data — this skill catches those changes.

## What to check

### 1. Read current configuration

Read `src/weatherbrief/fetch/variables.py` to understand:
- Which models are configured and their endpoints
- Which pressure levels each model uses
- Which surface/pressure variables are marked `unavailable_surface` / `unavailable_pressure`

### 2. Probe for newly available pressure levels

For each model, test whether levels **not currently configured** now return data.

**Candidate extra levels to test** (beyond what each model currently has):
- Upper atmosphere: 250, 200, 150, 100, 50 hPa
- Mid-level gaps: 975, 950, 900, 875, 825, 800, 775, 750, 725, 675, 650, 625, 575, 550, 525, 450, 350 hPa

For each model endpoint, request `temperature_{level}hPa` at a test point (e.g., lat=49.0, lon=2.5) with `forecast_days=1`. A non-null value at hour index 12 means the level is available.

```python
import json, urllib.request

def probe_levels(base_url, extra_params, candidate_levels):
    """Returns list of levels that return non-null temperature data."""
    available = []
    params_str = ",".join(f"temperature_{l}hPa" for l in candidate_levels)
    url = f"{base_url}?latitude=49.0&longitude=2.5&hourly={params_str}&timezone=UTC&forecast_days=1"
    for k, v in extra_params.items():
        url += f"&{k}={v}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    hourly = data.get("hourly", {})
    for level in candidate_levels:
        val = hourly.get(f"temperature_{level}hPa", [None]*13)[12]
        if val is not None:
            available.append(level)
    return available
```

### 3. Probe for newly available variables

For each model, test variables currently listed in `unavailable_surface` and `unavailable_pressure`:

**Surface variables to test:** `freezing_level_height`, `visibility`, `precipitation_probability`, `rain`, `showers`, `cape`, `wind_gusts_10m`

**Pressure-level variables to test:** `vertical_velocity` (at 700 hPa as a representative level)

```python
def probe_surface_vars(base_url, extra_params, var_names):
    """Returns list of surface variables that return non-null data."""
    available = []
    params_str = ",".join(var_names)
    url = f"{base_url}?latitude=49.0&longitude=2.5&hourly={params_str}&timezone=UTC&forecast_days=1"
    for k, v in extra_params.items():
        url += f"&{k}={v}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    hourly = data.get("hourly", {})
    for var in var_names:
        val = hourly.get(var, [None]*13)[12]
        if val is not None:
            available.append(var)
    return available
```

### 4. Check for new models on Open-Meteo

Probe these known Open-Meteo endpoints that we don't currently use:

| Model | Endpoint | Test URL |
|-------|----------|----------|
| ECMWF AIFS | `/v1/ecmwf` with `models=ecmwf_aifs025` | May not exist yet on OM |
| JMA GSM | `/v1/jma` | Japanese Met Agency |
| BOM ACCESS | `/v1/bom` | Australian Bureau of Met |
| ICON-EU (regional) | `/v1/dwd-icon` with `models=icon_eu` | Higher-res European |
| ARPEGE-HD | `/v1/meteofrance` with `models=arpege_europe` | Higher-res European |
| HRRR | `/v1/gfs` with `models=hrrr_conus` | High-res US regional |

For each, try a simple request for `temperature_2m` and report whether it returns data.

### 5. Check ECMWF Open Data direct access (optional)

If the `ecmwf-opendata` package is installed, also check what ECMWF provides directly that Open-Meteo doesn't:

```python
try:
    from ecmwf.opendata import Client
    client = Client(source="ecmwf")
    # List what's available for IFS and AIFS
except ImportError:
    pass  # Skip if not installed
```

Key variables to look for beyond Open-Meteo:
- `q` (specific humidity) — useful for icing
- `vo` (vorticity), `d` (divergence) — useful for CAT turbulence
- `100u`, `100v` (100m winds) — low-level jet detection
- `tcwv` (total column water vapor)

## Output format

Present results as a clear comparison table:

```
=== AUDIT RESULTS ===

## Pressure Levels
| Model    | Current levels | New levels available | Action needed? |
|----------|---------------|---------------------|----------------|
| ECMWF    | 11 (1000-150) | 100, 50 hPa        | Optional       |
| GFS      | 28 (1000-150) | none                | No             |
| ...      |               |                     |                |

## Surface Variables
| Model    | Currently unavailable      | Now returning data | Action needed? |
|----------|---------------------------|--------------------|----------------|
| ECMWF    | freezing_level, visibility | (none)             | No             |
| ...      |                           |                    |                |

## Pressure Variables
| Model    | Currently unavailable | Now returning data | Action needed? |
|----------|----------------------|--------------------|----------------|
| ICON     | vertical_velocity    | (still null)       | No             |
| ...      |                      |                    |                |

## New Models
| Model | Available? | Notes |
|-------|-----------|-------|
| AIFS  | No        | Not on Open-Meteo yet |
| ...   |           |       |

## ECMWF Direct (beyond Open-Meteo)
| Variable | Available? | Value for aviation? |
|----------|-----------|---------------------|
| q (specific humidity) | Yes | Icing calculations |
| ...      |           |                     |
```

Flag anything that has changed since last audit and suggest specific code changes to `variables.py` if new data is worth enabling.

## Tips

- Open-Meteo returns null (not an error) for unsupported variables — safe to probe
- Test at a well-covered location (Paris CDG: 49.0, 2.5) for reliable results
- Run this periodically (monthly) as providers keep opening data
- ECMWF has been steadily expanding open data — check their changelog at https://www.ecmwf.int/en/forecasts/datasets/open-data
