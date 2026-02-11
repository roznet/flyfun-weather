# Flight Weather Trend Tracker — Architecture & Spec

## Purpose

A tool for medium-range (D-7 to D-0) weather assessment for cross-country GA flights in Europe. The goal is NOT to replace a proper pre-flight brief — it's to build a daily evolving picture of whether a planned flight is likely flyable, uncertain, or unlikely, starting from a week out and increasing in detail as the flight date approaches.

The primary user is a VFR/IFR-capable single-engine pilot flying routes like EGTK→LSGS (Oxford to Sion), where alpine weather, icing, cloud bases, and winds aloft are critical decision factors.

## Core Concept

Given a **route** (origin, optional midpoint/alternate, destination), a **target date/time**, and a **cruise altitude**, the system:

1. Fetches quantitative forecast data from multiple models at key waypoints
2. Fetches human-written text forecasts (synoptic outlook, regional forecasts)
3. Generates Skew-T diagrams at selected waypoints
4. Computes derived aviation parameters (headwind/tailwind, icing bands, stability)
5. Compares models (GFS vs ECMWF vs others) to show convergence/divergence
6. Feeds everything to an LLM to produce a concise daily aviation weather digest
7. Tracks how the forecast evolves day-over-day

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                  Route Config                    │
│  origin: EGTK (51.836, -1.320)                  │
│  midpoint: LFPB (48.969, 2.441)                 │
│  destination: LSGS (46.219, 7.327)              │
│  date: 2026-02-21  time: 09:00Z                 │
│  cruise_alt: FL080  track: ~155° magnetic        │
└──────────────────────┬──────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌─────────────┐ ┌──────────┐ ┌──────────────┐
   │ Quantitative│ │   Text   │ │   Skew-T     │
   │ Data Fetch  │ │ Forecast │ │  Generator   │
   │             │ │  Fetch   │ │              │
   │ Open-Meteo  │ │ Met Off. │ │ Open-Meteo   │
   │  - GFS      │ │ DataPoint│ │ pressure lvl │
   │  - ECMWF    │ │ DWD      │ │ data → MetPy │
   │  - Ensemble │ │ MF Aéro  │ │              │
   └──────┬──────┘ └────┬─────┘ └──────┬───────┘
          │             │              │
          ▼             ▼              ▼
   ┌─────────────────────────────────────────────┐
   │              Analysis Engine                 │
   │                                              │
   │  - Headwind/tailwind computation             │
   │  - Icing band detection                      │
   │  - Cloud base/top estimation                 │
   │  - Stability indices (from Skew-T data)      │
   │  - Model comparison & divergence scoring     │
   │  - Ensemble spread → confidence metric       │
   │  - Day-over-day delta tracking               │
   └──────────────────┬──────────────────────────┘
                      │
                      ▼
   ┌─────────────────────────────────────────────┐
   │              LLM Digest Generator            │
   │                                              │
   │  System prompt: aviation weather expert      │
   │  Input: structured data + text forecasts     │
   │  Output: concise go/no-go assessment         │
   └──────────────────┬──────────────────────────┘
                      │
                      ▼
   ┌─────────────────────────────────────────────┐
   │              Output / Storage                │
   │                                              │
   │  - Daily digest (text + Skew-T PNGs)         │
   │  - Forecast history (JSON per day)           │
   │  - GRAMET PDF (from D-2 via Autorouter)      │
   │  - Trend report (how forecast evolved)       │
   └─────────────────────────────────────────────┘
```

---

## Data Sources

### 1. Open-Meteo Forecast API (Free, no key required)

**Base URL:** `https://api.open-meteo.com/v1/forecast`

Used for: primary quantitative data at each waypoint.

#### Surface/atmospheric variables (hourly)

```
temperature_2m, relative_humidity_2m, dewpoint_2m,
surface_pressure, pressure_msl,
wind_speed_10m, wind_direction_10m, wind_gusts_10m,
precipitation, precipitation_probability,
cloud_cover, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
freezing_level_height,
cape,
visibility
```

#### Pressure level variables (hourly)

Request at: 1000, 925, 850, 700, 600, 500, 400, 300 hPa

```
temperature_{level}hPa
relative_humidity_{level}hPa  (Note: not available at all levels on all models)
dewpoint_{level}hPa           (derive from temp + RH if not directly available)
wind_speed_{level}hPa
wind_direction_{level}hPa
geopotential_height_{level}hPa
```

**Dewpoint derivation:** If dewpoint is not directly available at a pressure level, compute from temperature and relative humidity using the Magnus formula:
```
γ = ln(RH/100) + (b × T) / (c + T)
Td = (c × γ) / (b - γ)
where b = 17.67, c = 243.5°C
```

#### Model-specific endpoints

For model comparison, fetch the same variables from different model endpoints:

| Model | Endpoint | Resolution | Range | Notes |
|-------|----------|------------|-------|-------|
| Best match (default) | `/v1/forecast` | ~11km | 16 days | Blends best available models |
| ECMWF IFS | `/v1/ecmwf` | 9km (native) | 10 days | Best European medium-range |
| GFS | `/v1/gfs` | 25km | 16 days | Good baseline comparison |
| DWD ICON | `/v1/dwd-icon` | 7km (EU) | 7 days | Best short-range for Europe |
| Météo-France AROME/ARPEGE | `/v1/meteofrance` | 1.5km/11km | 4/6 days | Excellent for France |

**Important:** Not all variables are available on all model endpoints. The implementation should gracefully handle missing variables.

#### Ensemble endpoint

**URL:** `https://ensemble-api.open-meteo.com/v1/ensemble`

Request same core variables with `&models=ecmwf_ifs025,gfs025` to get ensemble member spread.

Key ensemble variables:
```
temperature_2m, wind_speed_10m, wind_direction_10m,
precipitation, cloud_cover, wind_speed_850hPa, wind_direction_850hPa
```

The ensemble returns data for each member (51 for ECMWF, 31 for GFS). Compute:
- **Mean and standard deviation** across members for each variable
- **Spread** as a confidence indicator: low spread = high confidence
- **Percentiles** (10th, 25th, 50th, 75th, 90th) for key parameters

### 2. Open-Meteo for Skew-T Data

Use the same pressure level endpoints but specifically structured for Skew-T plotting.

**Required per waypoint:**

At pressure levels 1000, 925, 850, 700, 600, 500, 400, 300 hPa:
- Temperature (°C)
- Dewpoint temperature (°C) — derive from temperature + relative_humidity
- Wind speed (knots) and direction (°)
- Geopotential height (m)

**Skew-T generation** uses MetPy (`pip install metpy`):

```python
from metpy.plots import SkewT
from metpy.calc import lcl, lfc, el, parcel_profile, cape_cin
from metpy.units import units
import matplotlib.pyplot as plt
import numpy as np

def generate_skewt(pressure, temperature, dewpoint, u_wind, v_wind,
                   location_name, valid_time, output_path):
    """
    Generate a Skew-T Log-P diagram from model sounding data.

    Parameters:
        pressure: array of pressure levels (hPa)
        temperature: array of temperatures (°C) at each level
        dewpoint: array of dewpoint temperatures (°C) at each level
        u_wind: array of u-component of wind (knots)
        v_wind: array of v-component of wind (knots)
        location_name: string for title
        valid_time: datetime for title
        output_path: path for PNG output
    """
    # Attach units
    p = pressure * units.hPa
    T = temperature * units.degC
    Td = dewpoint * units.degC
    u = u_wind * units.knots
    v = v_wind * units.knots

    fig = plt.figure(figsize=(9, 10))
    skew = SkewT(fig, rotation=45)

    # Plot temperature and dewpoint
    skew.plot(p, T, 'r', linewidth=2, label='Temperature')
    skew.plot(p, Td, 'g', linewidth=2, label='Dewpoint')

    # Plot wind barbs
    skew.plot_barbs(p, u, v)

    # Compute and plot parcel profile
    parcel_prof = parcel_profile(p, T[0], Td[0]).to('degC')
    skew.plot(p, parcel_prof, 'k--', linewidth=1.5, label='Parcel')

    # Compute LCL, LFC, EL
    lcl_p, lcl_t = lcl(p[0], T[0], Td[0])
    skew.plot(lcl_p, lcl_t, 'ko', markersize=8, label=f'LCL {lcl_p:.0f}')

    try:
        lfc_p, lfc_t = lfc(p, T, Td)
        skew.plot(lfc_p, lfc_t, 'b^', markersize=8, label=f'LFC {lfc_p:.0f}')
    except:
        pass  # No LFC in stable atmosphere

    try:
        el_p, el_t = el(p, T, Td)
        skew.plot(el_p, el_t, 'rv', markersize=8, label=f'EL {el_p:.0f}')
    except:
        pass

    # Shade CAPE and CIN
    try:
        skew.shade_cape(p, T, parcel_prof)
        skew.shade_cin(p, T, parcel_prof)
    except:
        pass

    # Compute CAPE/CIN values
    try:
        cape_val, cin_val = cape_cin(p, T, Td, parcel_prof)
        cape_str = f"CAPE: {cape_val:.0f}  CIN: {cin_val:.0f}"
    except:
        cape_str = "CAPE/CIN: N/A"

    # Fiducial lines
    skew.plot_dry_adiabats(alpha=0.2, color='orangered')
    skew.plot_moist_adiabats(alpha=0.2, color='teal')
    skew.plot_mixing_lines(alpha=0.2, color='green')

    # Annotate icing band (0°C to -20°C)
    freezing_idx = np.argmin(np.abs(T.magnitude))
    minus20_idx = np.argmin(np.abs(T.magnitude + 20))
    if freezing_idx != minus20_idx:
        skew.ax.axhspan(p[freezing_idx].magnitude, p[minus20_idx].magnitude,
                        alpha=0.1, color='blue', label='Icing band (0 to -20°C)')

    skew.ax.set_xlim(-40, 40)
    skew.ax.set_ylim(1050, 250)
    skew.ax.set_title(f'{location_name}\nValid: {valid_time}\n{cape_str}',
                      fontsize=12)
    skew.ax.legend(loc='upper left', fontsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
```

**Skew-T waypoints:** Generate Skew-T diagrams at:
- Origin (departure time)
- Midpoint / alternate (ETA at midpoint)
- Destination (ETA at destination)
- Optionally: any point where terrain is critical (e.g. Alpine crossing point)

**For model comparison**, overlay two models on the same Skew-T (e.g., GFS temperature in red-dashed, ECMWF in red-solid) to visually show where they agree/disagree on atmospheric structure.

### 3. Text Forecast Sources

These provide the synoptic narrative that the LLM uses to contextualize the quantitative data.

#### Met Office DataPoint (Free, requires API key)

**Registration:** https://www.metoffice.gov.uk/services/data/datapoint

Provides:
- **Regional text forecasts** (5-day, updated twice daily): `http://datapoint.metoffice.gov.uk/public/data/txt/wxfcs/regionalforecast/json/{regionId}?key={API_KEY}`
- **National outlook** (up to 30 days): Available via the text forecast endpoints
- Region IDs: South East = 512, South West = 513, etc. Choose based on route departure area.

These forecasts contain prose like: *"A ridge of high pressure building from the southwest through Thursday..."* — exactly what the LLM needs.

#### UK Met Office GAMET (if accessible via MAVIS API or scraping)

GAMETs include:
- Met situation summary
- Freezing level with local variations
- Visibility, cloud, weather by zone
- Wind and temps at 1000ft, 3000ft, 6000ft
- Regional outlook (6h) and UK extended outlook (24h)
- Issued 4× daily: 0400, 1000, 1600, 2200 UTC

**Access:** Currently via the Aviation Briefing Service (retiring March 2026) or MAVIS. May need web scraping unless MAVIS exposes an API. Investigate MAVIS beta for programmatic access.

#### Météo-France Aéroweb (Free, requires registration)

**Registration:** Email webmaster.aeroweb@meteo.fr for a server identifier.

Provides via XML API:
- METAR/TAF for any ICAO airport
- TEMSI France and EUROC charts (images — can be passed to vision LLM for interpretation)
- WINTEM charts (wind/temp at altitude — images)
- "Maille Fine" AROME vertical cross-sections (excellent for mountain flying)

**Note:** TEMSI and WINTEM are image-based, not text. Two options:
1. Pass images to a vision-capable LLM for interpretation
2. Use as supplementary visual output alongside the text digest

#### DWD Open Data (Free, no registration)

German Weather Service publishes synoptic text forecasts covering European weather patterns.

**URL pattern:** `https://opendata.dwd.de/weather/text_forecasts/`

Includes SYNOP-style European area forecasts. These are in German but can be translated by the LLM.

#### Autorouter Briefing (from D-2)

Once within ~48-72h of departure, the Autorouter GRAMET becomes reliable:

```
GET https://api.autorouter.aero/v1.0/met/gramet?waypoints={wpts}&altitude={alt}&departuretime={timestamp}&totaleet={seconds}
```

Returns a PDF with the vertical cross-section showing clouds, icing, wind, turbulence along the route. Also:

```
GET https://api.autorouter.aero/v1.0/met/metartaf/{icao}
```

Returns JSON with METAR and TAF for any airport.

**Authentication:** Autorouter uses session cookies or OAuth. The implementation needs to handle login via the Autorouter API.

---

## Analysis Engine

### Headwind/Tailwind Computation

For each waypoint, at the planned cruise pressure level:

```python
import math

def compute_headwind_crosswind(wind_speed_kt, wind_dir_deg, track_deg):
    """
    Compute headwind/tailwind and crosswind components.

    Returns:
        headwind: positive = headwind, negative = tailwind
        crosswind: positive = from right, negative = from left
    """
    relative_wind = math.radians(wind_dir_deg - track_deg)
    headwind = wind_speed_kt * math.cos(relative_wind)
    crosswind = wind_speed_kt * math.sin(relative_wind)
    return headwind, crosswind
```

Compute for each leg of the route at cruise altitude. Provide both the component values and a simple summary (e.g. "15kt tailwind average en route").

### Icing Band Detection

Icing risk exists where:
- Temperature is between 0°C and -20°C (most dangerous: 0 to -10°C)
- Relative humidity > 70% (visible moisture / cloud likely)
- Cloud cover present at the relevant level

```python
def assess_icing_risk(temp_c, rh_pct, cloud_cover_pct):
    """
    Assess icing risk at a given pressure level.

    Returns: 'none', 'light', 'moderate', 'severe'
    """
    if temp_c > 0 or temp_c < -20:
        return 'none'
    if rh_pct < 60 or cloud_cover_pct < 10:
        return 'none'

    # SLD risk peaks near 0°C
    if -10 <= temp_c <= 0 and rh_pct > 80:
        severity = 'severe' if cloud_cover_pct > 70 else 'moderate'
    elif -20 <= temp_c < -10 and rh_pct > 80:
        severity = 'moderate' if cloud_cover_pct > 50 else 'light'
    else:
        severity = 'light'

    return severity
```

Report as altitude bands: "Moderate icing risk FL040-FL080 (temp -2°C to -12°C in cloud)."

### Cloud Base / Top Estimation

Open-Meteo does NOT provide cloud base/top directly. Estimate from pressure level data:

```python
def estimate_cloud_layers(pressure_levels, temperatures, relative_humidities,
                          geopotential_heights):
    """
    Estimate cloud base and top from pressure level RH profiles.

    Cloud likely where RH > 80%. Base = lowest such level, top = highest.
    Returns list of (base_ft, top_ft, coverage_estimate) tuples.
    """
    IN_CLOUD_THRESHOLD = 80  # percent RH
    cloud_layers = []
    in_cloud = False
    base_ft = None

    for i, (p, t, rh, z) in enumerate(zip(pressure_levels, temperatures,
                                            relative_humidities,
                                            geopotential_heights)):
        alt_ft = z * 3.28084  # meters to feet
        if rh >= IN_CLOUD_THRESHOLD and not in_cloud:
            in_cloud = True
            base_ft = alt_ft
        elif rh < IN_CLOUD_THRESHOLD and in_cloud:
            in_cloud = False
            cloud_layers.append((base_ft, alt_ft, 'estimated'))

    if in_cloud and base_ft is not None:
        cloud_layers.append((base_ft, pressure_levels[-1], 'estimated, top unknown'))

    return cloud_layers
```

**Limitation:** This is coarse — pressure levels are spaced ~1500m apart at low levels. The LCL from the Skew-T analysis provides a better cloud base estimate for convective cloud. For stratiform cloud, the RH profile method is the best we can do from free data.

**Alternative:** If Meteomatics free tier provides enough calls, their `cloud_base_agl` and `ceiling_height_agl` parameters are much more accurate. Worth investigating.

### Stability Assessment

Derived from Skew-T data via MetPy:

- **CAPE** (Convective Available Potential Energy): > 500 J/kg = convective risk, > 1500 = significant
- **CIN** (Convective Inhibition): Negative values indicate a "cap" suppressing convection
- **LCL** (Lifting Condensation Level): Predicted cloud base for surface-driven convection
- **LFC** (Level of Free Convection): If present, convection possible if air reaches this level
- **EL** (Equilibrium Level): Top of potential convective cloud
- **Lapse rate**: Compare environmental lapse rate to dry/moist adiabatic for stability
- **Temperature inversion**: Where temperature increases with altitude (stable layer, trapping)

### Model Comparison & Divergence

For each waypoint and time, fetch the same variables from multiple models and compute:

```python
def model_divergence(model_values: dict[str, float]) -> dict:
    """
    Compare values from different models.

    model_values: {'GFS': 12.3, 'ECMWF': 11.8, 'ICON': 12.1}

    Returns dict with mean, spread, agreement level.
    """
    values = list(model_values.values())
    mean = sum(values) / len(values)
    spread = max(values) - min(values)

    return {
        'mean': mean,
        'spread': spread,
        'models': model_values,
        'agreement': 'good' if spread < threshold else 'moderate' if spread < threshold*2 else 'poor'
    }
```

Thresholds (suggested, tune with experience):

| Variable | Good agreement | Poor agreement |
|----------|---------------|----------------|
| Temperature (°C) | < 2°C spread | > 5°C |
| Wind speed (kt) | < 5kt spread | > 15kt |
| Wind direction (°) | < 20° spread | > 60° |
| Precipitation (mm) | < 1mm spread | > 5mm |
| Cloud cover (%) | < 15% spread | > 40% |
| Freezing level (m) | < 200m spread | > 600m |

### Ensemble Confidence Scoring

```python
def ensemble_confidence(ensemble_members: list[float]) -> dict:
    """
    Compute confidence metrics from ensemble members.
    """
    arr = np.array(ensemble_members)
    return {
        'mean': np.mean(arr),
        'std': np.std(arr),
        'p10': np.percentile(arr, 10),
        'p25': np.percentile(arr, 25),
        'p50': np.percentile(arr, 50),
        'p75': np.percentile(arr, 75),
        'p90': np.percentile(arr, 90),
        'spread': np.percentile(arr, 90) - np.percentile(arr, 10),
        'confidence': 'high' if np.std(arr) < threshold else 'medium' if np.std(arr) < threshold*2 else 'low'
    }
```

### Day-over-Day Tracking

Store each day's analysis as a JSON snapshot. On subsequent runs, compare:

```python
def compute_trend(today: dict, yesterday: dict) -> dict:
    """
    Compare today's forecast for the target date with yesterday's forecast
    for the same target date.

    Returns dict of changes with direction and magnitude.
    """
    changes = {}
    for key in today:
        if key in yesterday and isinstance(today[key], (int, float)):
            delta = today[key] - yesterday[key]
            changes[key] = {
                'delta': delta,
                'direction': 'improving' if is_improvement(key, delta) else 'deteriorating',
                'significant': abs(delta) > significance_threshold(key)
            }
    return changes
```

What constitutes "improving" depends on the parameter:
- Wind: decreasing headwind = improving
- Cloud cover: decreasing = improving
- Precipitation probability: decreasing = improving
- Freezing level: rising = improving (for a given route altitude)
- Ensemble spread: narrowing = confidence improving

---

## LLM Digest Generation

### Input Assembly

Construct a structured prompt containing:

```
ROUTE: {origin} → {midpoint} → {destination}
DATE: {target_date} ({days_until} days from now)
ALTITUDE: {cruise_alt}
TRACK: {track_degrees}°

=== QUANTITATIVE DATA ===
[For each waypoint: surface conditions, pressure level data at cruise alt,
 headwind/tailwind, icing assessment, cloud layers, stability indices]

=== MODEL COMPARISON ===
[Key variables where models agree/disagree, with spread values]

=== ENSEMBLE CONFIDENCE ===
[Confidence metrics for key parameters]

=== TEXT FORECASTS ===
[Met Office regional forecast text]
[DWD synoptic text if available]
[GAMET extract if available]

=== SKEW-T SUMMARY ===
[For each waypoint: LCL, LFC, EL, CAPE/CIN, lapse rate assessment,
 inversion layers, icing bands from the sounding]

=== TREND (if not first day) ===
[How today's forecast differs from yesterday's for the same target date]
```

### System Prompt

```
You are an experienced aviation weather briefer for European GA operations.
You are briefing a competent pilot who understands aviation meteorology
including Skew-T interpretation, pressure systems, frontal analysis, and
icing theory. Do NOT over-simplify.

Produce a concise daily weather digest for the planned flight.

Structure:
1. OVERALL ASSESSMENT: One line — Green (likely go) / Amber (uncertain,
   watch) / Red (likely no-go) with one-sentence reason.

2. SYNOPTIC SITUATION: 2-3 sentences on the large-scale pattern (pressure
   systems, fronts, air mass) and how it's expected to evolve.

3. KEY FACTORS (for this specific route):
   - Winds: headwind/tailwind at cruise altitude, significant wind at
     other levels
   - Cloud & Visibility: expected bases/tops, layers, any low IMC risk
   - Precipitation & Convection: rain/snow probability, thunderstorm risk
     (CAPE context)
   - Icing: altitude bands at risk, severity, freezing level
   - Specific concerns: Alpine weather for Sion, foehn, valley fog,
     orographic effects, etc.

4. MODEL AGREEMENT: Where models agree/disagree. What depends on resolving
   current uncertainty.

5. TREND: How today's outlook compares to yesterday's. Is it converging
   toward a clear picture?

6. WATCH ITEMS: What to monitor in the next 24h that could change the
   assessment.

Be direct. Use aviation terminology. Say "I don't know" when the data is
genuinely uncertain rather than hedging everything. If the ensemble says
it's clearly fine, say so. If it's clearly unflyable, say that too.
```

### Output Format

```
═══════════════════════════════════════════════════
  EGTK → LFPB → LSGS    Sat 21 Feb 2026 0900Z
  Digest #5  (D-2)  Generated: Thu 19 Feb 18:00Z
═══════════════════════════════════════════════════

🟢 LIKELY GO — Ridge firmly established, models converging

SYNOPTIC: High pressure centered over the Bay of Biscay extending
NE across France. Light gradient across the route. The Azores high
retrograde expected to persist through Saturday.

WINDS: Light and variable at FL080 across most of the route.
Expect 5-10kt tailwind component southern France. No significant
wind concerns.

CLOUD: Scattered high cloud only (CI/CS around FL300). Clear below
FL150 en route. Morning valley fog possible at LSGS (Sion), clearing
by 0800Z — plan arrival after 1000L.

ICING: Freezing level ~6500ft. No cloud in the icing band. Negligible
risk at FL080.

CONVECTION: CAPE < 100 J/kg. Stable atmosphere. No CB risk.

MODELS: GFS and ECMWF in strong agreement on surface pressure,
cloud cover, and winds. Spread < 2kt on wind, < 5% on cloud cover.

TREND: Improving since D-5. Initial uncertainty around frontal
timing resolved — passage confirmed for Wednesday evening.
Forecast has been consistent for 3 days.

WATCH: Sion valley fog — check 0600Z TAF on Friday.
═══════════════════════════════════════════════════
```

---

## File & Storage Structure

```
flight-weather-tracker/
├── config/
│   ├── routes.yaml           # Saved route definitions
│   └── api_keys.yaml         # Met Office DataPoint key, Autorouter creds, etc.
├── src/
│   ├── fetch/
│   │   ├── open_meteo.py     # Open-Meteo API client (forecast + ensemble)
│   │   ├── text_forecasts.py # Met Office, DWD text fetch
│   │   ├── autorouter.py     # Autorouter GRAMET + METAR/TAF (D-2 onward)
│   │   └── aeroweb.py        # Météo-France Aéroweb (if registered)
│   ├── analysis/
│   │   ├── wind.py           # Headwind/tailwind/crosswind
│   │   ├── icing.py          # Icing band assessment
│   │   ├── clouds.py         # Cloud base/top estimation
│   │   ├── stability.py      # CAPE, LCL, stability from sounding data
│   │   ├── models.py         # Model comparison & divergence
│   │   ├── ensemble.py       # Ensemble spread & confidence
│   │   └── trend.py          # Day-over-day tracking
│   ├── skewt/
│   │   ├── fetch_sounding.py # Assemble sounding data from Open-Meteo
│   │   └── plot.py           # MetPy Skew-T generation
│   ├── digest/
│   │   ├── prompt.py         # LLM prompt assembly
│   │   └── generate.py       # LLM API call + output formatting
│   ├── models.py             # Pydantic models for route, waypoint, forecast data
│   └── main.py               # CLI entry point
├── data/
│   ├── forecasts/            # Raw API responses per day
│   │   └── 2026-02-21/
│   │       ├── d-5_2026-02-16/
│   │       │   ├── open_meteo_gfs.json
│   │       │   ├── open_meteo_ecmwf.json
│   │       │   ├── ensemble.json
│   │       │   ├── met_office_text.json
│   │       │   └── analysis.json
│   │       ├── d-4_2026-02-17/
│   │       └── ...
│   ├── skewt/                # Generated Skew-T PNGs
│   │   └── 2026-02-21/
│   │       ├── d-3_EGTK_gfs.png
│   │       ├── d-3_EGTK_ecmwf.png
│   │       ├── d-3_LSGS_gfs.png
│   │       └── ...
│   └── digests/              # Generated text digests
│       └── 2026-02-21/
│           ├── d-5.md
│           ├── d-4.md
│           └── ...
└── requirements.txt
```

### Route Configuration (routes.yaml)

```yaml
routes:
  egtk_lsgs:
    name: "Oxford to Sion"
    origin:
      icao: EGTK
      name: "Oxford Kidlington"
      lat: 51.8361
      lon: -1.3200
    midpoint:  # optional stop / alternate reference point
      icao: LFPB
      name: "Paris Le Bourget"
      lat: 48.9694
      lon: 2.4414
    destination:
      icao: LSGS
      name: "Sion"
      lat: 46.2192
      lon: 7.3267
    # Additional Skew-T waypoints (e.g. Alpine crossing)
    extra_skewt_points:
      - name: "Jura crossing"
        lat: 46.8
        lon: 6.1
    cruise_altitude_ft: 8000
    cruise_pressure_hpa: 750  # approximate, for level data
    track_deg: 155            # approximate average track
    estimated_eet_hours: 4.5  # for GRAMET request
```

---

## Phased Implementation

### Phase 1: Core data fetch + basic digest

- Open-Meteo client for GFS and ECMWF at 3 waypoints
- Basic wind, temperature, cloud, precipitation extraction
- Headwind/tailwind computation
- Simple text output with key numbers
- Store daily JSON snapshots

### Phase 2: Skew-T diagrams + icing/stability

- Fetch pressure level profiles at waypoints
- Generate Skew-T PNGs using MetPy
- Derive icing bands, LCL, CAPE, stability indices
- Overlay two models on comparative Skew-T

### Phase 3: Text forecasts + LLM digest

- Met Office DataPoint integration
- LLM prompt assembly with all data
- Generated daily markdown digest
- Day-over-day trend comparison

### Phase 4: Ensemble & model comparison

- Ensemble API integration
- Confidence scoring from spread
- Model divergence reporting
- Convergence tracking over days

### Phase 5: Autorouter integration + final polish

- Autorouter GRAMET fetch at D-2
- Autorouter METAR/TAF fetch at D-1
- Aéroweb integration (if registered)
- MCP server wrapper for conversational access
- Notification/alert on significant forecast changes

---

## Dependencies

```
# requirements.txt
requests>=2.31
pyyaml>=6.0
numpy>=1.24
matplotlib>=3.7
metpy>=1.5
pint>=0.22        # unit handling for MetPy
anthropic>=0.40   # or openai, for LLM digest generation
pydantic>=2.0
```

---

## CLI Interface

```bash
# Run daily digest for a saved route and target date
python -m flight_weather_tracker digest --route egtk_lsgs --date 2026-02-21

# Quick check for a route (no stored history)
python -m flight_weather_tracker check --from EGTK --to LSGS --date 2026-02-21 --alt FL080

# Generate Skew-T diagrams only
python -m flight_weather_tracker skewt --route egtk_lsgs --date 2026-02-21 --models gfs,ecmwf

# Show trend for an ongoing flight watch
python -m flight_weather_tracker trend --route egtk_lsgs --date 2026-02-21

# Run as MCP server (for integration with Claude or FlyFunBrief agent)
python -m flight_weather_tracker serve --mcp
```

---

## Future Considerations

- **MCP server mode**: Expose as an MCP tool so your aviation agent can call it conversationally
- **iOS integration**: Surface digest/Skew-T output in FlyFunBrief
- **Automated scheduling**: Cron job to run daily and push notifications on significant changes
- **Meteomatics integration**: If free tier sufficient, use their `cloud_base_agl` and `ceiling_height_agl` for better cloud data
- **TEMSI/WINTEM image analysis**: Pass Aéroweb chart images to a vision LLM for automated interpretation
- **Historical calibration**: Track forecast accuracy vs actual conditions to calibrate confidence levels over time
