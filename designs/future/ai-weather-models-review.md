# AI Weather Models Review

Assessment of ML-based weather models (GraphCast, GenCast, ECMWF AIFS, NeuralGCM,
Google WeatherNext 2/3) for use in the aviation weather pipeline. Reviewed April
2026; WeatherNext amendment September 2026.

**Decision:** Not pursuing integration at this time. The models lack critical
aviation-specific variables (cloud microphysics, icing, convective indices) and
have insufficient vertical resolution for sounding analysis.

**Status (re-checked against code 2026-09-08):** unchanged as a *sounding source*.
No AI model is fetched, decoded, or registered anywhere in the pipeline. The only
AIFS string in the codebase is in `fetch/grib/ecmwf_fetch.py`:
`parse_ecmwf_filename()` recognises the `aifs-ens` model token in ECMWF
dissemination filenames and `scan_ecmwf_files()` can filter on it. That is filename
parsing for completeness of the ECMWF feed format — nothing downstream ingests
AIFS. Do not read it as partial integration.

**Amendment 2026-09-08 (Google WeatherNext 2 / 3):** the April gap analysis still
holds for the *upper-air sounding*. WeatherNext's pressure-level set is the same
six variables on the same 13 levels that were rejected then, and it is 6-hourly.
But two things changed that the original review did not anticipate, and both are
narrow, supplementary and worth evaluating on their own merits rather than as a
"seventh model": a **station-trained 2m T/Td head at 0.05°** and an **easily
accessible 64-member ensemble**. See [WeatherNext (2026-09)](#weathernext-2--3-reviewed-2026-09-08) below.

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

## WeatherNext 2 / 3 (reviewed 2026-09-08)

Google DeepMind's WeatherNext family. "WeatherNext Graph" and "WeatherNext Gen"
in the April review above are the same models as GraphCast and GenCast, which
Google renamed into this family. WeatherNext 2 (WN2, Nov 2025) and WeatherNext 3
(WN3, Sep 2026) are the current generations.

Variable lists below come from primary sources, not marketing pages. WN2 is from
the model config in `google-deepmind/weathernext`
(`weathernext/weathernext2/configs/WeatherNext2.json`, `task.target_variables`
and `task.pressure_levels`); WN3 is from Table 1 and Table A.4 of the
WeatherNext 3 paper (arXiv:2609.03582).

### What they output

**Pressure-level (both WN2 and WN3), 13 levels, 0.25°, 6-hourly:**

`50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 850, 925, 1000 hPa`

| Short name | Variable |
|---|---|
| z | Geopotential |
| t | Temperature |
| q | Specific humidity |
| u, v | Wind components |
| w | Vertical velocity |

This is byte-for-byte the AIFS/GraphCast variable set the April review rejected.
WN3 raises the *hourly* step only for single-level fields plus three token
upper-air exceptions (`300/500 hPa t,z` and `1000 hPa u,v`). Everything else
aloft stays 6-hourly.

**Single-level, WN2 (0.25°):** `2t, 2d, 10u, 10v, 100u, 100v, msl, sst,
total_precipitation_6hr`, plus cyclone track/intensity heads.

**Single-level, WN3 (0.1°, hourly):** WN2's set plus `tcc, hcc, mcc, lcc`
(total/high/medium/low cloud cover), `cdir, fdir, ssrd` (solar radiation), and
`tp_1hr` (hourly precipitation, trained on IMERG and PARDI radar).

**Station head, WN3 only (0.05°, hourly, continuously queryable in space and
time):** `2t, 2d` decoded as a sparse continuous query conditioned on local
terrain, and **trained on in-situ surface observations**. The paper evaluates it
against global METAR and reports substantially lower error than analysis-based
forecasts. This is the one output with no counterpart in any physics model we run.

### Still absent: the fields the pipeline actually runs on

`clwc` / `ciwc` (no icing engine, since SFIP, Ogimet-NWP and `is_in_cloud_layer()`
all need condensate), per-level cloud fraction, `cape` / `cin` / `k` / `totalx`
(no convective track), visibility, ceiling, cloud base/top geometry, freezing
level, wind gusts, precipitation type, snowfall, surface pressure (only `msl`).

WN3's `lcc/mcc/hcc/tcc` are the one genuinely new aviation-adjacent field versus
the April review, and they are cover *amounts* with no geometry: no ceiling, no
base, no top. They cannot feed `build_nwp_cloud_layers_from_condensate` or the
ceiling path. At most they are another vote in a cover consensus we already take
from six models.

### Vertical resolution: unchanged verdict

13 levels puts a ~75 hPa hole between 1000 and 925 hPa. The pipeline's boundary-
layer work depends on resolving exactly that hole: `detect_inversions`, the
Richardson CAT tiers scaled by each layer's own Δz (meteorology-decisions §28),
the boundary layer measured from the model's own ground (§29) and the PBL-top
diagnosis (§31). WeatherNext cannot serve any of them.

### How to access it

| Channel | What you get | Cost / gate |
|---|---|---|
| **Open-Meteo `/v1/google-weathernext`** | WN2, 64 members, 13 levels, native 6-hourly interpolated to hourly, 15 days. Only the 00/12z runs (06/18z miss Open-Meteo's schedule). RH and wind speed/direction derived from the native `q` and `u/v`; cloud cover **derived by Open-Meteo**, not native to WN2. | Same terms as our other Open-Meteo models, no paperwork. |
| **BigQuery / Earth Engine** | WN3 surface fields: ensemble mean plus `_p10/_p25/_p50/_p75/_p90`, not raw members. No pressure levels. | WeatherNext Data Request form, ~5-7 business days. Standard GCP query cost. |
| **GCS full-ensemble Zarr** | WN3 all 64 members including pressure levels: the **only** channel carrying upper air, and only for the 6-hourly 00/06/12/18z inits. | Same form. **Requester Pays**, bucket in `us-east1`, so egress is billed unless compute runs in-region. We run neither compute nor storage on GCP. |
| **Google Maps Platform Weather API** | WN3-backed point forecasts: temperature, dewpoint, humidity, pressure, visibility, wind including gust, cloud cover, precipitation type. Up to 240 h hourly. | $0.15 / 1000 calls, 10k/month free. A commercial licence, not the experimental terms. |

**Latency.** WN3 interim (hourly) cycles target init +7h10 in GCS, +7h25 in
BigQuery/Earth Engine. So the *hourly refresh does not make a run arrive sooner*
than ECMWF (`_ECMWF_OFFSET` = 6h40), and ICON-D2 at +2h is far fresher. What the
hourly cadence does buy is worst-case forecast age: the newest available data is
never older than ~8h, against ECMWF's ~12h40 just before the next 6-hourly cycle
lands.

**Licensing, to read before shipping anything to pilots.** Real-time WeatherNext
data (under 1 h old; on BigQuery, under 48 h) falls under the *GDM Real-Time
Weather Forecasting Experimental Data Terms of Use*, not an open licence. Only
the historical tail is CC BY 4.0. A briefing product consuming the real-time feed
is exactly the case those terms govern, so they must be read in full before any
user-facing use. The Maps Platform Weather API exists precisely as the licensed
commercial route, and is the safer channel if WN3 output ever reaches a briefing.

### Verdict

**Do not add WeatherNext as a seventh model slot.** A model slot in this pipeline
owes a sounding: cross-sections, Skew-T, per-model advisory grading, the icing and
convective tracks. WeatherNext delivers none of that, and a slot that silently
grades NONE on icing and convective because the fields are absent is worse than no
slot. That is the same failure mode `convective_scheme_absent` was added to
prevent for HRRR.

Two narrower ideas do survive, in priority order:

1. **WN3 station head for airport 2m T/Td (0.05°, hourly, METAR-trained).**
   The only thing here that no physics model gives us: a forecast bias-corrected
   against the station network at the airports we brief. It feeds density
   altitude, and dewpoint depression as a fog/low-ceiling proxy, in
   `analysis/airport_conditions.py` and `analysis/airport_consensus.py`, surfaces
   that already take a per-model consensus and so can absorb one more member
   without touching the sounding path. Blocked on the access form, and worth a
   verification run against the `verify/` and `era5/` harness before anything
   user-facing.

2. **WN2 64-member ensemble as a confidence signal at D-5..D-7.**
   `compare_models` currently infers confidence from spread across six
   *deterministic* models, which is a proxy for forecast uncertainty rather than a
   measurement of it. A real 64-member ensemble measures it. Testable today via
   Open-Meteo with no paperwork, as a supplementary confidence input rather than a
   graded model. This is April's revisit trigger 3, now met.

   One caveat must be respected: WN2's native step is 6-hourly and Open-Meteo
   interpolates to hourly. Grading an intermediate hour against interpolated
   ensemble members would break the same honesty invariant
   `compute_model_coverage` / `refused_times` enforces in timing-scenarios.

Neither idea justifies the GCS Requester-Pays upper-air path. If WeatherNext ever
earns a sounding slot, the trigger is condensate and CAPE, not resolution.

---

## When to Revisit

This decision should be reconsidered if:

1. **AIFS adds cloud microphysics variables** — ECMWF has stated plans to expand
   AIFS output variables in future versions. If clwc/ciwc are added, AIFS becomes
   a viable model for our pipeline.

2. **AIFS increases vertical resolution** — if the model moves to 25+ pressure
   levels with reasonable boundary-layer spacing.

3. **~~GenCast ensemble becomes easily accessible~~ (MET, 2026-09).** Open-Meteo
   now serves WeatherNext 2's 64 members at `/v1/google-weathernext` with no
   access request. Probabilistic uncertainty quantification is testable today; see
   idea 2 in the WeatherNext section. Note the members are 6-hourly natively, so
   any hourly value is interpolated.

4. **Open-Meteo adds AIGFS with derived cloud fields** — if their specific-humidity
   to cloud-cover derivation proves skillful, GraphCast's 37 levels with derived
   clouds could be worth evaluating as a consensus model.

5. **WeatherNext adds condensate or CAPE.** WN3 added cloud *cover* but no
   `clwc`/`ciwc` and no CAPE/CIN. Those two, not resolution, are what would make a
   WeatherNext sounding slot worth building.

The `audit-sources` skill already carries an AIFS probe row (`/v1/ecmwf` with
`models=ecmwf_aifs025`), so running that skill is the cheapest way to detect
triggers 1, 2 and 4 without re-reading provider release notes by hand. It does
**not** yet probe `/v1/google-weathernext`; add that row when trigger 5 is worth
watching automatically.

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
