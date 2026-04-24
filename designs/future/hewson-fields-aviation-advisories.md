# Hewson Diagnostic Fields as Aviation Advisories

> Using the Hewson 1998 / Hewson & Titley 2010 diagnostic fields (θe, |∇θe|, −∇²θe, TFP, −V·∇θe, ∂θe/∂t) as **aviation-weather advisories in their own right**, independent of whether we ever finish a robust front-detection algorithm.
>
> **Living reference doc** — captures the motivation, design decisions, reasoning, and phased plan so that future choices are informed by earlier thinking even if specific decisions change.

## 1. Core insight

We have spent substantial effort trying to detect "is there a front here?" at zone scale, which is hard at 0.5° resolution and at best matches DWD's drawn fronts. **The DWD chart is already public** and is what forecasters look at — reproducing it isn't the pilot's actual need.

What a GA pilot actually needs is: *"how will the weather change along my route, and when?"* — and every Hewson-derivative field answers a slice of that directly, without claiming to locate fronts.

**Shift of goal**: from "detect synoptic fronts" → to "derive actionable per-leg advisories from thermal/dynamic fields".

Key implication: **we don't need the front detection to be correct.** The fields are physically meaningful and forecast-skillful on their own. Shipping a map and a set of leg-level advisories sidesteps the whole Hewson calibration problem.

## 2. Each field's aviation meaning

### 2.1 θe (K, at level) — air-mass label

- **> 320 K**: tropical/subtropical air — summer convection, thunderstorm potential especially if θe drops fast with height (convective instability)
- **290–310 K**: normal mid-latitude air
- **< 280 K**: cold polar/arctic — expect showers + icing in cloud
- **Route use**: a 15–20 K jump in θe between waypoints ≈ flying between air masses, regardless of drawn fronts.

### 2.2 |∇θe| (K/100 km) — air-mass boundary intensity

| Magnitude | Aviation meaning |
|---|---|
| < 2 | benign — uniform air mass |
| 2–4 | noticeable transition — minor wind shift, some mid-level cloud |
| 4–8 | significant boundary — wind shift, low-level turbulence, cloud band, possible precipitation |
| > 8 | classical frontal zone — stratus/nimbostratus, precipitation, shear with altitude |
| > 12 | sharp front — SIGMET-worthy |

Useful *even when no front is drawn* — pre-frontal moisture tongues, jet-entrance zones, sea-breeze boundaries.

### 2.3 −∇²θe (K/(100 km)²) — sharpness of transition

Answers "is the weather change abrupt or gradual?":

- **> 2**: sharp — narrow-band precipitation, quick wind shift over a few nm
- **≈ 0**: smooth gradient — if there's weather it's spread over 100+ km
- **< 0**: gradient plateau — you're inside a broad transition, not at its edge

Distinguishes "I'll hit a squall line" from "ceilings will drop over 2 hours".

### 2.4 TFP sign — which side of the transition

- **TFP > 0**: warm side approaching gradient max → conditions **deteriorating**
- **TFP ≈ 0**: at sharpest part of zone → peak conditions
- **TFP < 0**: cold side moving away → conditions **improving**

Compact per-leg label: "deteriorating" / "peak" / "improving".

### 2.5 −V·∇θe (K/h) — warm/cold advection (the most flight-relevant)

**Warm advection (> 0)**
- Moist/warm air overrunning → stratiform cloud, persistent rain
- **Ceilings lower over time**, visibility often reduces
- **Icing risk rises**
- "Depart before the warm front arrives"

**Cold advection (< 0)**
- Polar/drier air arriving → **improving visibility**, unstable showery
- **Icing risk drops**
- Gusty winds, mechanical turbulence
- "Wait for cold front to clear, then VMC"

Thresholds:
- |−V·∇θe| > 1 K/h: significant tendency
- > 2 K/h: rapid change — frontal passage within 1–2 h

### 2.6 ∂θe/∂t (K/h) — observed tendency at a fixed point

Includes diurnal/radiative effects. "What's actually going to change at my destination over the next 3 hours?"

- **Rising**: warm sector arriving — ceilings likely to drop
- **Falling**: post-frontal clearing or evening cooling
- **~ Zero**: stable — current METAR pattern persists

## 3. Proposed per-leg advisories

Deterministic, zone-independent, framework-free — they sidestep the whole front-detection calibration problem. A pilot sees "deteriorating ceilings on leg 3" without us needing to claim "there's a warm front".

| Advisory | Field(s) | Trigger |
|---|---|---|
| *Air-mass transition on this leg* | \|∇θe\| along route | > 6 K/100 km |
| *Sharp weather edge* | −∇²θe | > 2 K/(100 km)² |
| *Deteriorating ceilings expected* | −V·∇θe | > 1 K/h sustained ≥ 2 h |
| *Improving conditions arriving* | −V·∇θe | < −1 K/h |
| *Convective outlook* | θe + lapse rate | θe > 315 K + steep lapse aloft |
| *Destination tendency* | ∂θe/∂t @ airport | \|∂θe/∂t\| > 0.5 K/h over 3 h |

## 4. Decisions and their reasoning

### 4.1 Resolution: **0.25°** (decided 2026-04-24)

Options considered: 0.5° (current frontal-detection default) vs 0.25° (Open-Meteo native for ECMWF, GFS, ICON).

Benefit of 0.25° concentrates where we most need it — in **the derivative fields**:

| Field | Gain from 0.25° |
|---|---|
| θe magnitude | minimal (smooth thermodynamic field) |
| \|∇θe\| | moderate |
| **−∇²θe** | **large** — 2nd derivative noise-dominated at 0.5° |
| **TFP** | **large** — also a 2nd derivative |
| Advection | moderate |
| Tendency | none (independent of spatial resolution) |

And critically: per-leg discrimination. A 50 nm GA leg ≈ 90 km ≈ **1.6 cells at 0.5° vs 3.3 cells at 0.25°** — the latter actually resolves transitions across a typical GA leg.

**Cost at 0.25°**: ~4× the storage, ~4× the compute, same API model-cost. Absolute numbers (see §5) still tiny. Accepted.

### 4.2 Pressure levels: **925 / 850 / 700 hPa** (3 levels, chosen 2026-04-24)

Covers ~2,500 ft → ~10,000 ft — roughly 90% of European GA cruise. Open-Meteo provides T / Td / wind at all three.

- **925 hPa** (~2,500 ft MSL): low-level flight, circuit, mountain flying approach layers
- **850 hPa** (~5,000 ft MSL): the classical synoptic level, most GA cruise
- **700 hPa** (~10,000 ft MSL): upper-range GA cruise, turbo/oxygen flights

Deferred: **600 hPa** (~14,000 ft) — few GA operations up there; revisit if demand emerges.

### 4.3 Forecast horizon: **120 h** (5 days)

Matches what our briefings already use. Open-Meteo delivers 120–240 h for the 3 models.

### 4.4 Cadence: **2 cycles/day/model** (00Z + 12Z)

ECMWF's main runs; GFS and ICON also publish 00Z/12Z as primary. Between cycles, briefings read the last snapshot; 12 h staleness is fine for smooth advective fields.

### 4.5 Storage format: **NPZ now → Zarr when needed**

**Start with NPZ** (numpy's native compressed binary zip):
- Simplest thing that works — `np.savez_compressed(f, gradient=..., advection=...)`
- One file per model per cycle
- Zero extra dependencies
- ~85 MB raw → ~25 MB compressed per snapshot

**Migrate to Zarr** (chunked n-dim array store) if any of:
- Briefings get slow because they load full snapshot to read one level / 12h
- We want to serve subsets over the network (S3 / CDN)
- Multiple concurrent readers cause file-locking friction

Hide the format behind `load_snapshot(model, init_time)` / `save_snapshot(...)` so the swap is local.

### 4.6 Retention: **48-hour file cache** (same pattern as GRIB)

Recent two cycles on disk per model; older cycles purged. Matches the existing pattern in `fetch/` for GRIB.

### 4.7 Fields stored per snapshot

- **θe** (raw, K)
- **gradient** (|∇θe|, K/100 km)
- **neg_laplacian** (−∇²θe, K/(100 km)²)
- **tfp** (K/(100 km)²)
- **advection** (−V·∇θe, K/h)
- **tendency** (∂θe/∂t, K/h — needs h±1)

`dT_dx`, `dT_dy` (gradient components) are *not* stored — re-derived if ever needed.

## 5. Data-volume sizing

Per model per cycle at **0.25° × 3 levels × 120 h × 6 fields × float16**:

| Metric | Value |
|---|---|
| Grid points per level per hour | 19,493 (101 lat × 193 lon) |
| Values per snapshot | 1.78 M × 6 = 10.7 M |
| Raw bytes (float16) | ~85 MB |
| Compressed NPZ | ~25 MB |
| Latest state, all 3 models | ~75 MB |
| Daily write volume | ~150 MB |
| Fetch wall-time per cycle | ~1.5 min |
| Compute wall-time per cycle | ~5 min |
| 48 h cache footprint | ~300 MB |

Small enough that the "precompute" pipeline can keep everything in a handful of files and load the whole snapshot into memory when a briefing needs it.

## 6. Architecture

### 6.1 Precompute pipeline (offline, cron)

```
scheduled twice a day per model
  ↓
fetch_multi_level_grid(model, init_time, levels=[925,850,700])
  ↓  (Open-Meteo chunked, ~30 requests per model)
compute_hewson_diagnostics() on each (level, hour)
  ↓
np.savez_compressed("data/hewson/<model>/<init>.npz",
                     gradient=..., advection=..., ...)
  ↓
retain last 2 cycles, purge older
```

### 6.2 Briefing read path (on-demand)

```
briefing pipeline → load_snapshot(model, latest_init)
  ↓
sample_hewson_at_route(snapshot, route_points)
  ↓                                  ↑
  (tri-interpolation: lat/lon, log-p, hour)
  ↓
per-waypoint + per-leg summaries → advisory evaluators
  ↓
advisories.json + cross-section + map layer
```

### 6.3 Route sampling — three axes

**Spatial** — bilinear on the grid at the two bounding levels:
```python
F(lat_wp, lon_wp) = bilinear(F_grid, lat_wp, lon_wp)
```

**Vertical** — linear in log(p), since pressure decreases exponentially:
```python
w = (log(p_wp) − log(p1)) / (log(p2) − log(p1))
F_wp = (1 − w) · F(p1) + w · F(p2)
```
Caveat for derivatives: fronts tilt with height, so |∇θe| at 850 and at 700 aren't spatially co-located. Still OK to interpolate the value pointwise — the altitude-specific weather is what matters.

**Temporal** — linear between forecast hours H and H+1 based on ETA seconds. Already the `WaypointForecast` pattern in `fetch/`.

### 6.4 Output granularities

| Granularity | Purpose |
|---|---|
| Per-waypoint values | cross-section overlay, Skew-T side panel |
| Per-leg max / mean / integral | advisory evaluators |
| Route-wide scalars | LLM digest context |

## 7. Interactive Hewson map layer

A new layer on the existing **forecast-page** (see `designs/forecast-page.md`) that lets pilots visualize the Hewson fields geographically.

### 7.1 Why it's valuable beyond the advisories

- **Trust**: "the advisory says warm advection on leg 3 — and I can see the red lobe on the map". Verifies the recommendation.
- **Context the advisory misses**: convergence 50 nm south of the route might affect diversion/alternate choice.
- **Teaches intuition**: pilots learn which fields matter for which weather, which pro forecasters know and GA pilots usually don't see.

### 7.2 Per-request size

At 0.25° (101×193), one metric × one level × one hour:

| Encoding | Size |
|---|---|
| float16 binary | 39 KB |
| JSON array | ~80 KB |
| Quantized int8 + scale | ~20 KB |
| PNG heatmap | ~30–50 KB |

Loading 120 h for one metric × level ≈ 5 MB — fine for an on-demand map page.

### 7.3 Endpoint contract (sketch)

```
GET /api/hewson-map?model=ecmwf&init=<t>&level=850&metric=advection&hour=24
  → JSON or binary array of shape (n_lat, n_lon)
```

One lookup into the precompute snapshot. ~1 ms response.

### 7.4 Frontend controls

- **Metric dropdown**: θe, |∇θe|, −∇²θe, TFP, −V·∇θe, ∂θe/∂t (6 options; grouped by purpose)
- **Level selector**: `925 / 850 / 700` hPa — with pilot-friendly labels `2,500 / 5,000 / 10,000 ft`
- **Time slider**: reuse the one from model-accuracy heatmap
- **Opacity slider**: stack under existing per-airport markers
- **Colormap**: baked per metric (diverging RdBu for advection/tendency, sequential YlOrRd for gradient) — **same mapping as the `plot-hewson` CLI** so debug + pilot-facing views are identical.

### 7.5 Rendering

**Canvas overlay** — one colored rect per grid cell, ~19k cells in a single draw call. Same pattern as cross-section. Leaflet `L.CanvasLayer`.

At low zoom, cells are visible as pixelated boxes. Accepted — scientifically honest, shows the actual grid resolution rather than pretending to more precision.

### 7.6 Legal

Carry over the existing "advisory-only / not for operational use" disclaimer pattern from the rest of the briefing.

## 8. Accuracy expectations

Being honest about what we'd be claiming:

| Output | Accuracy expectation |
|---|---|
| Advection **sign** (warm vs cold) | > 90% |
| Advection **magnitude** | ±30–50% at best |
| Threshold-crossing timing | ±1–2 hours |
| Gradient magnitude | ±20% |
| TFP zero-crossing location | ±50–100 km (resolution-limited) |

An advisory like "*warm advection on leg 3 — ceilings likely to lower over next 2–4 h*" is defensible. "*Ceilings will drop from 2,500 ft to 1,200 ft at 14:15*" is not. Advisories stay qualitative.

## 9. Validation plan

Leverage existing `metar-taf-accuracy` infrastructure (see `designs/metar-taf-accuracy.md`):

- For each advisory emitted, check whether the METAR trend over the following 3 h matched the prediction
- "Warm advection > 1 K/h" → did observed dew-point / ceiling / visibility trend consistently over 3 h?
- Monthly rollup → skill score (POD / FAR) per advisory type
- Dashboard: the existing verification digest extended with a "Hewson advisories" track

Gives us real numbers for "how often does the system call warm advection when warm advection actually happened" — the right answer to any accuracy question.

## 10. Why isn't anyone else doing this?

It's a **product / UX gap, not a scientific gap**.

### 10.1 The meteorology is textbook

θe advection, TFP, Hewson fronts — standard in operational forecaster training at MF, DWD, UKMO, ECMWF. Nothing novel scientifically.

### 10.2 Commercial ops tools have it at forecaster level

DTN, Baron, Meteologix, Weatherous → sophisticated interactive tools, but target **forecasters** with raw fields, not pilots with automated advisories.

### 10.3 Mass-market aviation tools don't

- **Windy**: shows 500 hPa θe and advection as overlays — closest comparison — but leaves interpretation 100% to user, no route-awareness, no advisories
- **ForeFlight / Jeppesen / SkyDemon / EasyVFR**: stick to METAR/TAF/SIGWX/SIGMET
- **aviationweather.gov / ADDS**: standardized product catalogue only
- **PogoAirports, SAIL, FlyConditions**: basic isobar/wind only

### 10.4 Why the gap persists

1. **Regulatory conservatism** — official aviation weather = METAR/TAF/SIGMET; derived advisories are legally weaker "flight planning aids"
2. **Accuracy-vs-trust tradeoff** — say "ceilings will drop" wrongly once and users lose trust; commercial products prefer "look at this field, you decide"
3. **Market size** — GA pilots worldwide ~500k, "derived advisory" users much smaller; limited commercial incentive
4. **Data accessibility** — gridded forecasts were painful to fetch until Open-Meteo / Herbie / ECMWF open charts made it easy (~2022+); the niche is newer than it seems
5. **Translation is domain-specific** — "θe advection 1.2 K/h at 700 hPa" → "ceilings drop over 2 h at Basel" is manual work, no shared library does it

### 10.5 Our angle

Not trying to be MF or DWD. Building **GA-pilot-facing advisories with plain-English output, route-aware, tied to the pilot's actual flight**. Genuinely underserved because no commercial stack has both the aviation focus and the willingness to ship derived advisories.

## 11. Phased rollout

Phases are independent and shippable one at a time. Each produces something testable.

### Phase 1 + 2 — DONE (2026-04-24, PR #91)

- `compute_hewson_diagnostics()` in `detect.py` returning all Hewson fields
- `plot-hewson` CLI subcommand rendering 2×3 panel figure
- Calibration UX: `redraw-zones` CLI + color-coded DWD zone overlay
- CLI-only, not in the briefing pipeline

### Architecture consolidation — DONE (2026-04-24, PR #91)

What was pivotal about this session — we made the pipeline source-agnostic before investing in calibration:

- **Universal 0.25° grid**: `FRONTAL_GRID.resolution = 0.25`; 19,493-point European grid aligned with ERA5's default regridded output and Open-Meteo's 0.25° lat-lon grid
- **ERA5 loader**: `src/weatherbrief/era5/loader.py` — reads ERA5 GRIB, converts SI units, derives θe, returns the same field dict shape as `reshape_to_fields()`
- **Unified case format**: `src/weatherbrief/frontal/case.py` — `Case` dataclass with `load_case()`, `save_case_meta()`, `save_model_fields()`. Each case carries its own grid (`meta.json`) and per-model NPZ. Works for both Open-Meteo forecast cycles (multiple models, hourly) and ERA5 analyses (single "era5" model, 6-hourly)
- **Source-agnostic CLI**: `new-case --source {open_meteo,era5}`; `plot-hewson`, `score`, `diagnose`, `redraw-zones` all accept any model present in the case including `era5`
- **ERA5 download pipeline**: `scripts/smoke_era5_hewson.py` (CDS request validated), `scripts/download_era5_hewson.py` (not yet written — see Phase A pickup)

### Phase A — Route sampling on single-level data ✅ DONE (2026-04-24)

**Shipped**:
- `src/weatherbrief/frontal/route_sampling.py` — `sample_hewson_at_route(case, model, waypoints, hours)` returning per-waypoint dict of `{lat, lon, hour, theta_e, gradient, tfp, neg_laplacian, advection, tendency}`. Bilinear spatial interp + linear temporal interp between bounding available hours. Fractional hours supported. Out-of-grid or out-of-range returns NaN per field (not an exception).
- `route-hewson` CLI subcommand — resolves ICAO codes via `weatherbrief.airports.resolve_waypoints` (uses `data/nav.db`); prints per-waypoint table with thresholds color-coded per §3.
- `tests/test_frontal_route_sampling.py` — 12 tests covering bilinear math (linear-field exactness, cell-center averaging, out-of-grid NaN) and full sampling glue (integer hours, fractional interp, per-waypoint hours, unknown model, range guards).
- Smoke-tested against Storm Ciarán ERA5 case: cold advection at LFPG behind front, warm advection + rising tendency at LOWW ahead of it.

**Goal (original)**: validate spatial + temporal interpolation with zero API cost, on the data we already have.

**What already exists:**
- `Case.fields(model, hour)` returns `(n_lat, n_lon)` arrays per hour — clean input for sampling
- `Case.lat`, `Case.lon` — grid coords from `meta.json`
- The Storm Ciarán ERA5 case at `data/calibration/2023-11-02_era5_ciaran/` works end-to-end

**What to build:**
- `src/weatherbrief/frontal/route_sampling.py` — new module
  - `sample_hewson_at_route(case, model, route_points) -> list[dict]`
  - Bilinear spatial interpolation on the Case's lat/lon grid
  - Linear temporal interpolation between adjacent `available_hours`
  - Per-waypoint output: `{lat, lon, hour, gradient, tfp, neg_laplacian, advection, tendency}`
- New CLI `route-hewson --case <dir> --model <m> --waypoints "LFPG LFSB LIMC"`
  - Resolves waypoints via existing `rzflight` / `KnownAirports` (see design doc `rzflight`)
  - Prints per-waypoint field values with thresholds colored
- **Unit tests** for the interpolation math: known linear field → known analytic sampling result

**Pickup checklist for Phase A**:
1. Open `src/weatherbrief/frontal/case.py` — see `Case.fields(model, hour)` + `Case.available_hours(model)`
2. Open `src/weatherbrief/frontal/detect.py` — `compute_hewson_diagnostics()` is the function we're sampling outputs of
3. Resolve waypoint lat/lon via `rzflight` — existing project, see `mcp__library-docs__get_design_doc library=rzflight topic=waypoints`
4. Sample outputs into a structured dict; render with `python -m weatherbrief.frontal.cli route-hewson ...`
5. Add tests in `tests/frontal/test_route_sampling.py` with synthetic linear/sinusoidal fields

### Phase B — Multi-level ERA5 fetch + precompute pipeline

**Goal**: production-grade precompute at 0.25° × 925/850/700 hPa.

**What exists:**
- `scripts/smoke_era5_hewson.py` on the ERA5 server (`brice@server:/mnt/data/downloads/era5_download/`) — proven to fetch one day × 3 levels × 4 times in ~1 min
- `load_era5_fields(grib, timestamp, level_hPa)` accepts any of 925/850/700 — multi-level is a call-site change, not a loader change

**What to build:**
- `scripts/download_era5_hewson.py` — adapt `download_era5_t850.py` on the server. Loop over months (configurable `--from-month YYYY-MM --to-month YYYY-MM`), with `--max-concurrent` like the Z500 downloader. Store as one monthly GRIB per file (same pattern).
- Bulk fetch on server: **Oct 2024 → Mar 2025** (6 months, winter/spring — captures Atlantic cyclone season). Expected size ~150-200 MB. Wall-time ~1 hour.
- Rsync to local `data/era5/hewson/`
- Extend `build_case_from_era5` to accept `level_hPa: list[int]` and store multi-level NPZ:
  ```
  raw/era5.npz:
      T_925, Td_925, theta_e_925, u_925, v_925,   # 925 hPa
      T_850, Td_850, theta_e_850, u_850, v_850,   # 850 hPa
      T_700, Td_700, theta_e_700, u_700, v_700,   # 700 hPa
      valid_times
  ```
- `Case.fields(model, hour, level_hPa=850)` optional level argument (defaults to 850 for back-compat)

**Live-data precompute** (future split-off):
- New `src/weatherbrief/hewson/precompute.py` runs on cron: fetch → compute → NPZ snapshot per model per cycle
- Cadence 00Z + 12Z per model
- Retention: 48 h (see `fetch.md` for the existing retention pattern)

### Phase C — Advisory evaluators

**Goal**: the six advisories from §3 wired into `evaluate_all()`.

- Six new evaluators in `src/weatherbrief/advisories/evaluators/` following the existing registry pattern (see `designs/advisories.md`)
- Tri-axis sampling: for each route point × flight altitude (mapped to pressure level) × ETA → get Hewson field values
- Per-leg aggregation: max / mean / integral of each field across segment sample points
- Threshold logic per §3 table
- Integration: new advisories show up alongside the existing 14 in the briefing

### Phase D — Interactive map layer

**Goal**: forecast-page visualization per §7.

- Backend `/api/hewson-map?model=...&init=...&level=...&metric=...&hour=...` endpoint
- Frontend layer on `WeatherMap` component (see `designs/forecast-page.md`)
- Metric / level / time / opacity controls
- Colormap synced with `plot-hewson` CLI debug plot

**Phase ordering note**: C and D are independent after B. For early pilot feedback, **ship D first** — the map is more visually compelling and helps validate Phase B output without the advisory-wording churn.

### Phase E (future, speculative)

- **600 hPa** added if demand justifies
- **METAR-trend validation** track in the verification digest
- **Native GRIB** ingest (ECMWF Cycle 50r1 already on the droplet, ICON-EU full) to remove Open-Meteo interpolation layer
- **Cross-section layer** for advection / tendency (new layer in `visualization`)
- **Surface θe** for convective outlook (needs 2 m T + Td)
- **wetter3.de archive integration** — replace live DWD download with archive pull when case `--date` is in the past (currently the new-case CLI downloads today's DWD chart regardless of case date)

## 12. Status snapshot

| Item | Status |
|---|---|
| **Phase 1 + 2 (Hewson diagnostics + CLI)** | ✅ Done (2026-04-24) |
| **Architecture consolidation (0.25°, ERA5 loader, unified Case)** | ✅ Done (2026-04-24) |
| Resolution decision | ✅ 0.25° |
| Level decision | ✅ 925 / 850 / 700 hPa |
| Cadence decision | ✅ 2×/day (00Z, 12Z) |
| Storage decision | ✅ NPZ (Zarr deferred) |
| Retention decision | ✅ 48 h cache |
| ERA5 smoke test | ✅ Storm Ciarán 2023-11-02 validated |
| ERA5 bulk fetch | ✅ Done (1 year, 2025-02 → 2026-02, on `/mnt/data/downloads/era5_download/data/hewson/` — pending rsync to `data/era5/hewson/`) |
| **Phase A** (route sampling) | ✅ Done (2026-04-24) — `route_sampling.py`, `route-hewson` CLI, unit tests all green |
| Phase B (multi-level + precompute) | Requires Phase A |
| Phase C (advisory evaluators) | Requires Phase B |
| Phase D (map layer) | Requires Phase B; can ship before C |
| wetter3.de archive (retrospective DWD charts) | Known gap — not blocking |

## 12a. Quick pickup from fresh context

If you're resuming this work in a new session, here's the minimum to load:

```bash
# From repo root
# 1. Read the current state of the case pipeline
python -m weatherbrief.frontal.cli --help
# → see new-case / plot-hewson / score / redraw-zones

# 2. Reproduce Storm Ciarán end-to-end (GRIB already on disk):
python -m weatherbrief.frontal.cli plot-hewson \
    --case data/calibration/2023-11-02_era5_ciaran \
    --model era5 --hour 12 --field theta_e
# → data/calibration/2023-11-02_era5_ciaran/hewson_era5_theta_e_T12.png

# 3. To re-fetch ERA5 (server-side):
ssh brice@server
cd /mnt/data/downloads/era5_download
. venv/bin/activate
python smoke_era5_hewson.py --output-dir ./data/ --date 2024-01-22
# rsync -avz brice@server:/mnt/data/downloads/era5_download/data/era5_hewson_smoke_2024-01-22.grib data/era5/
```

**Key files** for Phase A pickup:
- `src/weatherbrief/frontal/case.py` — Case abstraction (start here)
- `src/weatherbrief/frontal/detect.py` — `compute_hewson_diagnostics()` (math we're sampling)
- `src/weatherbrief/frontal/cli.py:_cmd_plot_hewson` — closest existing CLI pattern
- `src/weatherbrief/era5/loader.py` — if touching level logic
- This doc — decisions and rationale

**Open questions** intentionally left for Phase A:
- How to resolve waypoints (ICAO/IATA codes vs lat/lon tuples)? Use `rzflight`'s `KnownAirports`.
- Per-leg aggregation: sample N points between waypoints? N = 10 gives ~5 nm resolution on a 50 nm leg, matches our 0.25° grid.
- Tendency when adjacent hours not present? Forward or backward diff — already handled in `_cmd_plot_hewson`.

## 13. Related docs

- `designs/frontal-detection.md` — zone-scale detection work this builds on
- `designs/future/frontal-detection-plan.md` — original plan (deliberately simpler than Hewson)
- `designs/future/front-calibration.md` — calibration workflow and scoring
- `designs/advisories.md` — where the new advisories plug in
- `designs/analysis-metrics.md` — where these fields appear in the metric catalog
- `designs/forecast-page.md` — where the new map layer lives
- `designs/metar-taf-accuracy.md` — validation infrastructure we'll reuse
- `designs/fetch.md` — grid-fetch and caching patterns
