# AI Weather Models Review

Assessment of ML-based weather models (GraphCast, GenCast, ECMWF AIFS, NeuralGCM)
for use in the aviation weather pipeline. Reviewed April 2026.

**Decision:** Not pursuing integration at this time. The models lack critical
aviation-specific variables (cloud microphysics, icing, convective indices) and
have insufficient vertical resolution for sounding analysis.

**Status (re-checked against code 2026-08-15):** unchanged — no AI model is
fetched, decoded, or registered anywhere in the pipeline. The only AIFS string in
the codebase is in `fetch/grib/ecmwf_fetch.py`: `parse_ecmwf_filename()` recognises
the `aifs-ens` model token in ECMWF dissemination filenames and `scan_ecmwf_files()`
can filter on it. That is filename parsing for completeness of the ECMWF feed
format — nothing downstream ingests AIFS. Do not read it as partial integration.

---

## Models Reviewed

### GraphCast / NOAA AIGFS

- **Source:** Google DeepMind, run operationally by NOAA as "AIGFS"
- **Access:** Free GRIB2 on AWS Open Data (CC0); also served by Open-Meteo
- **Resolution:** 0.25 deg (~28 km), 6-hour steps, 16 days
- **Pressure levels:** 37 (1000–1 hPa) natively; NOAA distributes a subset
- **Pressure-level variables (6):** temperature, u/v wind, geopotential, specific humidity, vertical velocity
- **Surface variables (5):** 2m temperature, 10m u/v wind, MSLP, total precipitation
- **Open-Meteo model name:** available via GFS endpoint (NOAA AIGFS feed)

### GenCast

- **Source:** Google DeepMind (ensemble/probabilistic)
- **Access:** Google Earth Engine as "WeatherNext Gen" (request form required)
- **Resolution:** 0.25 deg, 12-hour steps, 15 days, multiple ensemble members
- **Pressure levels:** 13
- **Variables:** Similar to GraphCast but fewer levels. No cloud cover.
- **Not yet served** by NOAA, ECMWF, or Open-Meteo operationally

### ECMWF AIFS

- **Source:** ECMWF's own ML model, operational since Feb 2025
- **Access:** Free via ECMWF Open Data (CC BY 4.0 since Oct 2025); also on Open-Meteo
- **Resolution:** 0.25 deg, 6-hour steps, 15 days
- **Pressure levels:** 13 (this is the model's native resolution, not a distribution limit):
  `1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50 hPa`
- **Open-Meteo model names:** `ecmwf_aifs025_single` (deterministic), `ecmwf_aifs025_ensemble` (50 members)
- **Commercial subscription:** Same data — since Oct 2025 all ECMWF data is open; commercial only adds delivery infrastructure. Can request via Product Requirements Editor with `class=ai, model=aifs-single`.

**AIFS pressure-level variables (6):**

| Variable | Available |
|---|---|
| Temperature (t) | Yes |
| U/V wind (u, v) | Yes |
| Geopotential (z) | Yes |
| Specific humidity (q) | Yes |
| Vertical velocity / omega (w) | Yes |
| Relative humidity | No (only specific humidity) |

**AIFS surface variables (20):** 2m temp/dewpoint, 10m & 100m wind, surface pressure,
MSLP, total/convective precipitation, snowfall, cloud cover (tcc/hcc/mcc/lcc),
skin temperature, total column water, radiation (ssrd/strd), runoff.

**AIFS does NOT output:** cloud liquid/ice water content, CAPE, CIN, visibility,
wind gusts, precipitation type, boundary layer height.

### NeuralGCM

- **Source:** Google, Apache-licensed code + CC BY-SA 4.0 weights
- **Access:** Self-hosted only (Python + GPU/TPU). No API or aggregator serves it.
- **Resolution:** 0.7 deg (~78 km) — too coarse for our use
- **Variables:** Standard atmospheric (T, wind, humidity, geopotential)
- **Not practical** for operational use without significant infrastructure

---

## Gap Analysis vs Pipeline Requirements

Our pipeline requires ~70 variables across pressure levels and surface for aviation
sounding analysis, cross-sections, icing assessment, and convective risk evaluation.

### Critical gaps in all AI weather models

| Requirement | GraphCast | AIFS | GenCast | Why it matters |
|---|---|---|---|---|
| Cloud liquid water (CLWMR) | No | No | No | Required for SFIP icing index |
| Cloud ice water (ICMR) | No | No | No | Required for icing type + precipitation phase |
| Cloud cover at pressure levels | No | No | No | Per-level cloud detection for ceiling/layers |
| CAPE / CIN | No | No | No | Convective risk assessment |
| Visibility | No | No | No | VFR/IFR determination |
| Relative humidity at levels | No | Derived | No | Cloud detection via dewpoint depression |
| Freezing level | Derivable | Derivable | Derivable | Icing zone boundaries |
| Wind gusts | No | No | No | Surface wind advisory |
| Precipitation type | No | No | No | Rain/snow/mixed classification |

### Vertical resolution

| Model | Levels | Spacing in boundary layer | Adequacy |
|---|---|---|---|
| GFS (Open-Meteo) | 28 | 25 hPa | Good |
| ICON-EU (GRIB) | 40 | ~25 hPa | Excellent |
| IFS HRES (commercial) | 25+ | ~50 hPa | Good |
| **AIFS** | **13** | **75 hPa** | **Too coarse** |
| **GraphCast** | **37** | Varies | Acceptable for dynamics, but missing variables |

13 levels is inadequate for aviation soundings — the 75 hPa gap between 1000 and
925 hPa means we miss the entire low-level structure where icing, turbulence, and
ceiling information is most critical.

---

## What AI Models Are Good At

These models excel at **large-scale dynamics**: synoptic patterns, temperature
advection, jet stream position, pressure systems, and broad wind fields. ECMWF's
benchmarks show AIFS matching or beating IFS HRES on 500 hPa geopotential and
upper-level temperature forecasts, especially at longer lead times (5–10 days).

They are fundamentally **dynamical core replacements** — they learned the fluid
dynamics but not the parameterized physics (cloud microphysics, boundary layer
turbulence, convection, radiation). Those diagnostic fields are what aviation
weather depends on most.

---

## When to Revisit

This decision should be reconsidered if:

1. **AIFS adds cloud microphysics variables** — ECMWF has stated plans to expand
   AIFS output variables in future versions. If clwc/ciwc are added, AIFS becomes
   a viable model for our pipeline.

2. **AIFS increases vertical resolution** — if the model moves to 25+ pressure
   levels with reasonable boundary-layer spacing.

3. **GenCast ensemble becomes easily accessible** — probabilistic forecasts could
   add value for uncertainty quantification in briefings (e.g., "30% chance of
   icing") even without full variable coverage, as a supplementary signal.

4. **Open-Meteo adds AIGFS with derived cloud fields** — if their specific-humidity
   to cloud-cover derivation proves skillful, GraphCast's 37 levels with derived
   clouds could be worth evaluating as a consensus model.

The `audit-sources` skill already carries an AIFS probe row (`/v1/ecmwf` with
`models=ecmwf_aifs025`), so running that skill is the cheapest way to detect
triggers 1, 2 and 4 without re-reading provider release notes by hand.

---

## Current Model Priority

For pipeline improvements, the priority remains traditional NWP with full physics:

1. **ICON-EU GRIB** — 40 native model levels (35–74) with QC/QI, already integrated.
   The icon slot now upgrades to **ICON-D2** (2.2 km, levels 16–65) whenever the whole
   route fits its domain — including explicit-convection diagnostics no AI model has.
2. **IFS HRES via commercial order** — 25 pressure levels with clwc/ciwc; ECMWF
   direct-GRIB delivery live and integrated (`fetch/grib/ecmwf_fetch.py`, scheduler
   watcher, alternates visibility source)
3. **GFS GRIB** — 28 levels with CLWMR/ICMR, already integrated; the gfs slot upgrades
   to **HRRR** (3 km, 35 levels) inside the CONUS domain
4. **Open-Meteo multi-model** — 6 models for consensus (`ecmwf`, `gfs`, `icon`, `ukmo`,
   `meteofrance`, `gem` in `fetch/variables.py`), already integrated

Each of those upgrades moved the pipeline *further* toward parameterized-physics
fields (condensate, explicit convection, gust/ceiling diagnostics) — i.e. away from
exactly what the AI models don't produce. The gap analysis above has widened, not
narrowed, since April 2026.
