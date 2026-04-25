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

### 4.4a Snapshot temporal resolution: **3 h stride** (decided 2026-04-24, after live GFS validation)

Open-Meteo delivers hourly; we decimate to every 3rd forecast hour before the Hewson compute loop. At 120 h horizon that is 40 timesteps per snapshot instead of 120.

Tradeoffs considered on the real GFS 12 Z cycle:

| Stride | Timesteps | NPZ size | Compute time | Slider UX | Advisory blur |
|---|---|---|---|---|---|
| 1 h | 120 | 139 MB | ~190 s | smoothest | ~15 km |
| **3 h** | **40** | **46 MB** | **~65 s** | mainstream (matches Windy/SYNOP) | **~50 km** |
| 6 h | 21 | 24 MB | ~30 s | sparse | ~100 km |

**Picked 3 h** because:
- ~50 km interpolation blur on a moving front is well inside the "±50–100 km TFP positional uncertainty" ceiling stated in § 8 (we cannot claim more precision than that anyway)
- Matches SYNOP reporting cadence, which is also what pro forecaster tools ship
- 3× size and 3× compute savings over 1 h, without the advisory-accuracy penalty 6 h introduces
- Per-flight advisories and cross-section bands that want finer granularity compute from per-briefing GRIB at flight time (§ 6.2 stencil path), not from the synoptic-view snapshot

**Not picked**: 1 h was expressive but expensive (139 MB × 12 snapshots = 1.7 GB peak on disk, ~13 min/cycle). 6 h was cheap but the spatial blur on fast-moving fronts was past the advisory error budget.

The NPZ stores the stride in a `stride_hours` field so readers can self-describe without hard-coded assumptions. Overridable via `python -m weatherbrief.hewson precompute --stride-hours N` for debug / high-fidelity one-off runs.

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

**Measured on the real GFS 2026-04-24 12 Z cycle** at `0.25° × 3 levels × 120 h horizon × 6 metrics × float32`, with **3 h stride** (see § 4.4a) so 40 timesteps land in the snapshot:

| Metric | Measured (1 model, 3 h stride) | Full cycle (3 models) |
|---|---|---|
| Grid points per timestep per level | 19,493 (101 × 193) | — |
| Timesteps (120 h / 3 h) | 40 | — |
| Raw bytes (float32, 18 metric stacks) | ~57 MB | ~170 MB |
| Compressed NPZ | **~46 MB** | ~140 MB (3 snapshots) |
| Fetch wall-time | ~65 s | ~3 min |
| Compute wall-time | ~65 s | ~3 min |
| Full-cycle wall time | ~130 s | **~6.5 min** |
| 48 h on-disk peak (2 cycles × 3 models) | — | **~550 MB** |

Earlier drafts of this section quoted "~25 MB compressed / ~300 MB peak / ~6.5 min total" — that was based on **float16 × 1 h stride** (which we intentionally avoid for precision) combined with a miscounting of the horizon (stated 120 h but math used 1 h). Above is the real footprint at the shipped defaults.

**Historical calibration on real data**:
- 1 h stride × float32 × 120 h = 139 MB / snapshot (single GFS run). Too big for the map payoff.
- 3 h stride × float32 × 40 timesteps = **46 MB / snapshot** (measured). Shipped default.
- 6 h stride × float32 × 21 timesteps ≈ 24 MB / snapshot. Considered; rejected because a 6 h gap blurs advection interpolation across ~100 km of front motion, which weakens the advisory-evaluator accuracy budget in § 8.

Advisory evaluators and cross-section bands that need finer temporal resolution compute directly from per-briefing GRIB at flight time (§ 6.2 stencil path), not from this synoptic-view snapshot.

## 6. Architecture

### 6.1 Precompute pipeline (dedicated scheduler loop)

**Key decision (2026-04-24, revised)**: the frontal-detection module is CLI-only — there is **no existing 6 h cron** to piggyback on (the earlier draft of this section assumed one; that was wrong). Hewson precompute is therefore a **new, independent loop** inside `scheduler.py`, following the same pattern as the five existing loops (`run_retention_loop`, `run_verification_loop`, `run_standalone_verification_loop`, `run_digest_loop`, `run_ecmwf_watcher_loop`).

**Timing**: fires at **05 Z** and **17 Z**, giving ~5 h after each 00 Z / 12 Z init for Open-Meteo to publish all three models, while keeping a 1 h margin before the 06 Z / 18 Z `run_standalone_verification_loop` full cycles (heavy forecast fetch + scoring) to avoid CPU/network overlap. Other loops in the scheduler are light enough not to matter (ECMWF watcher is disk-only, retention is once/day, etc.).

**Disable switch**: `DISABLE_HEWSON_PRECOMPUTE=1` (mirrors the existing `DISABLE_*` flags).

**Shared entry point**: the async loop and the `python -m weatherbrief.hewson precompute` CLI both call the same `weatherbrief.hewson.precompute.run_once(...)` — one implementation, two surfaces. Ad-hoc debugging, manual re-runs, and scheduled runs exercise identical code.

```
run_hewson_precompute_loop          ┐
  (scheduler.py, 05 Z / 17 Z)       │→ weatherbrief.hewson.precompute.run_once(...)
python -m weatherbrief.hewson       │      ↓
  precompute [--model / --dry-run / │   fetch_grid_fields(levels=[925, 850, 700])
   --force / --output-dir]          ┘      ↓
                                        reshape_to_fields per (hour, level)
                                           ↓
                                        compute_hewson_diagnostics + tendency
                                           ↓
                                        np.savez_compressed(
                                            "${DATA_DIR}/hewson/<model>/<init_iso_z>.npz",
                                            theta_e_{925,850,700},
                                            gradient_{925,850,700},
                                            neg_laplacian_{925,850,700},
                                            tfp_{925,850,700},
                                            advection_{925,850,700},
                                            tendency_{925,850,700},
                                            valid_times, lat, lon,
                                            init_time_unix, levels,
                                            stride_hours,   # = 3 by default
                                        )
                                           ↓
                                        purge_old_snapshots(retention_hours=48)
```

**Source-agnostic**: the fetch goes through `fetch_grid_fields` in `frontal/grid.py`, so when native-GRIB ingestion for ECMWF/ICON/GFS lands it's a call-site swap inside that function — `run_once` and everything downstream is unchanged.

**Filename format**: ISO 8601 with `Z` suffix (`2026-04-24T12:00:00Z.npz`) — matches the ECMWF watcher manifests, sortable, human-readable.

**Data directory**: `${DATA_DIR:-data}/hewson/<model>/` — follows the convention used by `storage/snapshots.py`, `tasks/outputs.py`, etc. In production `DATA_DIR=/mnt/flyfun_data/weather/data`.

**Terrain mask caching**: the SRTM3 build is ~seconds; we cache it to `${DATA_DIR}/hewson/terrain_mask.npz` on first run and load thereafter (shape + coords verified on load so grid changes invalidate the cache).

Snapshot size at 0.25° × 101×193 × 3 levels × 40 timesteps × 6 metrics × float32 ≈ **57 MB raw / ~46 MB compressed** per model per cycle (measured on the 2026-04-24 12 Z GFS run at shipped defaults). With 48 h retention × 3 models × 2 cycles/day ≈ **~550 MB on disk peak**. See § 5 for the full table and § 4.4a for why 3 h stride not 1 h.

### 6.2 Read paths (on-demand)

Three surfaces consume the precompute snapshot — each reads what it needs:

```
Map layer (§7):
    /api/hewson-map → full (n_lat, n_lon) grid at one (model, init, level, hour, metric)

Cross-section bands (§7.9, Open-Meteo era):
    bilinear-sample snapshot at each waypoint × {925, 850, 700}
    → 3 discrete-altitude bands overlaid on the existing cross-section canvas

Cross-section stencil (§7.9, GRIB era):
    per-briefing stencil in the native GRIB at each waypoint × all 25+ native pressure levels
    → continuous-altitude Hewson on the cross-section
    (Gated on native-GRIB ingestion; NOT from the precompute grid)

Advisory evaluators (Phase C):
    sample_hewson_at_route(snapshot, route_points) + altitude-appropriate level
    → per-leg max/mean → §3 thresholds → advisories.json
```

Rationale for the stencil split: the precompute is tuned for the map (3 levels is enough — pilot picks one at a time). The cross-section wants continuous altitude, which the 3-level precompute can't give. The briefing pipeline already parses the full-native-levels GRIB per route for cloud rendering — a stencil extraction around each waypoint at those same levels costs almost nothing.

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
- **Level selector**: `925 / 850 / 700` hPa — with pilot-friendly labels `2,500 / 5,000 / 10,000 ft`. Explicitly 3 levels, not 5: 500/400 hPa are upper-IFR and out of scope for GA.
- **Time slider**: reuse the one from model-accuracy heatmap
- **Opacity slider**: stack under existing per-airport markers
- **Colormap**: baked per metric (diverging RdBu for advection/tendency, sequential YlOrRd for gradient) — **same mapping as the `plot-hewson` CLI** so debug + pilot-facing views are identical.
- **Per-metric info popover** — a small (i) icon on the metric dropdown. Tapping opens the pilot-facing interpretation for that metric (ranges, decision rules), drawn from §2. This surfaces as "learning in context" so pilots can absorb meaning while they use the tool rather than reading a separate guide.

### 7.5 Rendering

**Canvas overlay** — one colored rect per grid cell, ~19k cells in a single draw call. Same pattern as cross-section. Leaflet `L.CanvasLayer`.

At low zoom, cells are visible as pixelated boxes. Accepted — scientifically honest, shows the actual grid resolution rather than pretending to more precision.

### 7.6 Legal

Carry over the existing "advisory-only / not for operational use" disclaimer pattern from the rest of the briefing.

### 7.9 Cross-section Hewson overlay

Sibling to the map layer — overlays Hewson information on the existing route cross-section canvas (`web/ts/visualization/cross-section/renderer.ts`, layer-registry pattern). Adds new `CrossSectionLayer` implementations alongside the existing cloud / icing / CAT / inversion layers.

**Two-phase build-out**:

**Phase D.1 (Open-Meteo era — now)**:
- Reads the **precompute snapshot** at each waypoint × {925, 850, 700} via bilinear-sample
- Renders 3 horizontal **bands** at 2,500 / 5,000 / 10,000 ft, coloured by the selected metric
- Same colormap as the map (consistency)
- Metric picker shared with the map — changing the map metric changes the cross-section bands
- Cost: <1 ms per briefing (just three bilinear samples per waypoint)

**Phase D.2 (GRIB era — later, gated on native-GRIB ingestion)**:
- Per-briefing **stencil** sampling: around each waypoint, extract a 3×3 (or 5×5 for cleaner 2nd derivatives) stencil from the briefing's already-loaded GRIB at each of the **25+ native pressure levels** the ingestion subsets
- Compute Hewson diagnostics on the stencil per waypoint × per level
- Gives **continuous-altitude Hewson** on the cross-section (not just 3 bands)
- Cost: tens of ms even for a 20-waypoint route × 25 levels, because GRIB is already parsed and stencil math is trivial
- Reuses the same `CrossSectionLayer` surfaces — UI doesn't change, data source upgrades silently

The 3-level precompute is kept small on purpose: it exists to feed the map, and the map shows one level at a time anyway. Adding levels to the precompute just to support the cross-section would be wasteful when the cross-section can draw from richer per-briefing data.

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

## 10a. Known limitations — what Hewson alone does not tell you

Surfaced during the 2026-04-24 retrospective pass. None are blockers for shipping Phase D, but each points at a follow-up track.

### 10a.1 Hewson ≠ cloud

`|∇θe|` measures thermal air-mass boundaries at one pressure level. It **doesn't** directly measure cloud presence. A dry cold front and a moist cold front produce the same gradient magnitude; only one comes with IFR ceilings.

Implication: a loud map might overlay a **dry** synoptic feature that had no operational impact. Or: a boring map might hide **local IMC** (morning stratus, fog). In the 27/28-Apr retrospective pair, the actual blocker on the 27th was local IMC at LFMD which the 850 hPa θe field simply didn't see — it's above the stratus layer.

**Fix track (Phase E moisture cross-check)**:
- Compute `RH₉₂₅` from existing T + q (zero new fetch — we already have what we need)
- Fetch ERA5 / briefing-pipeline **low cloud cover (LCC)** and **total precipitation (TP)** — single-level fields, small additional bytes
- Optionally **CAPE** for convective outlook
- Combined filter: "Hewson loud **and** LCC > X **and** RH₉₂₅ > Y" → real weather. "Hewson loud, moisture low" → dry ribbon, probably flyable.

### 10a.2 850 hPa is too high for low IMC

850 hPa ≈ 5,000 ft. A morning stratus layer at 1,500 ft or fog below the inversion won't show in `θe₈₅₀`. The map is fundamentally about **free-atmosphere** weather, not boundary-layer weather.

Implication: never claim the map tells you about local ceilings/vis. That's METAR/TAF territory. Hewson's job is to say "a significant weather *system* is approaching/over your route" — surface conditions need separate inputs.

### 10a.3 Route-aggregator sensitive to single cells

The retrospective scripts use `grad_max` (max across 12 route sample points) to summarise a route — which is dominated by one outlier cell. The route-mean / P95 is more honest. Proposed tweak for the analysis scripts:

```
grad_summary = P95(sample_points)   # instead of max(…)
```

Single-line change; keep the pair-wise cancel test (currently 3/3) as regression.

### 10a.4 Excitement score over-indexes on summer θe

Current score fires `convective-outlook` at `θe > 315 K`, which flags most summer flights regardless of actual convection. `θe` alone can't predict convection without **lapse rate** (which we'd need a second pressure level to compute, or CAPE). Two options:
- Raise threshold to `θe > 325 K` (tuned to pilot-calibrated cancellations)
- Drop the convective term from the score until we have CAPE

Both park until we do the Phase E moisture work.

---

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

**Shipped (2026-04-24):**
- `scripts/smoke_era5_hewson.py` on the ERA5 server — proven to fetch one day × 3 levels × 4 times in ~1 min
- `scripts/download_era5_hewson.py` on the server — monthly loop, one GRIB per month
- Bulk fetch done: 1 year 2025-02 → 2026-02 (~700 MB, rsynced to `data/era5/hewson/`)
- `load_era5_fields(grib, timestamp, level_hPa)` accepts any of 925/850/700
- **Multi-level Case storage**: `build_case_from_era5(..., level_hPa: int | list[int])`, `Case.fields(model, hour, level_hPa=...)`, `Case.available_levels(model)`. NPZ uses flat level-suffixed keys (`T_925, T_850, T_700, Td_*, theta_e_*, u_*, v_*`) for multi-level; legacy single-level 850 format preserved for back-compat (cases with no `levels` field in meta.json default to `[850]`). Inner dict keys stay `T850, Td850, theta_e, u850, v850` regardless of actual level — `850` is historical, values are from the requested level. Tests in `tests/test_frontal_case.py`.

**Still to build:**
- CLI: `new-case --source era5 --levels 925,850,700` — wire the list arg through to `build_case_from_era5` (currently accessible only via library).
- Rebuild the Ciarán case at 3 levels to prove the path end-to-end on a real debug case.

**Live-data precompute** (future split-off, gated on Phase D):
- New `src/weatherbrief/hewson/precompute.py` runs on cron: fetch → compute → NPZ snapshot per model per cycle
- Cadence 00Z + 12Z per model
- Retention: 48 h (see `fetch.md` for the existing retention pattern)

### Phase D — Interactive map + cross-section overlay (NEXT)

**Goal**: ship forecast-page visualization per §7 (map overlay) + §7.9 (cross-section bands), so pilots can see the Hewson fields *now*, without waiting for the advisory-text calibration loop.

**Rationale for D before C**: the map + cross-section expose the raw signal with pilot-facing tooltips (§7.4, §2). This:
- Gives pilots useful information from day one (teaching intuition by display)
- Lets us observe which fields actually matter for *their* decisions before we commit thresholds in code (Phase C)
- Validates Phase B's precompute output end-to-end without advisory-wording churn
- Sidesteps the current calibration blocker (moisture gap — see §10a.1)

**Phase D.0 — Precompute loop** ✅ DONE (2026-04-24, [task #8]):
- New `run_hewson_precompute_loop` in `scheduler.py`, firing at **05 Z** / **17 Z** (avoids collision with the 06 Z / 18 Z `run_standalone_verification_loop` full cycles — see §6.1)
- New module `src/weatherbrief/hewson/` with `run_once()` as the shared entry point for the loop and the `python -m weatherbrief.hewson precompute` CLI (one implementation, two surfaces)
- Generalised `fetch_grid_fields` / `reshape_to_fields` in `frontal/grid.py` to accept `levels=[925, 850, 700]` with 850-only back-compat for the frontal CLI
- Computes `theta_e, gradient, neg_laplacian, tfp, advection, tendency` at **925/850/700 hPa**
- Saves snapshot NPZ per `(model, init)` under `${DATA_DIR}/hewson/<model>/<init_iso_z>.npz`, retains 48 h
- ISO 8601 Z filenames, terrain mask cached to disk, `DISABLE_HEWSON_PRECOMPUTE=1` escape hatch
- Source-agnostic: when native-GRIB ingestion lands it's a call-site swap inside `fetch_grid_fields`

**Phase D.1 — Backend endpoint** ([task #9]):
- `GET /api/hewson-map?model=ecmwf&init=<t>&level=850&metric=advection&hour=24` → JSON or binary grid of shape (n_lat, n_lon)
- Reuses pack-access auth pattern from `packs.py`

**Phase D.2 — Map layer** ([task #10]):
- First **gridded** Leaflet CanvasLayer on the forecast page (today the forecast map is point-only, so this is the reusable pattern for future gridded overlays)
- Controls per §7.4: metric dropdown, 3-level selector with ft labels, hour slider, opacity
- Per-metric info popover tied to §2 pilot interpretations

**Phase D.3 — Cross-section bands** ([task #11]):
- New `CrossSectionLayer` implementations per §7.9 Phase D.1
- Bilinear-samples the precompute snapshot at each waypoint × {925, 850, 700}
- Draws 3 horizontal bands at 2,500 / 5,000 / 10,000 ft, shared colormap with map
- Shares metric picker with the map — changing one changes the other

**Phase D.4 — Stencil-in-GRIB era** ([task #12], later, gated on native-GRIB ingestion):
- Per-briefing stencil sampling at all 25+ native pressure levels per §7.9 Phase D.2
- Turns the cross-section's 3 discrete bands into continuous altitude — silently, via data-source swap

### Phase C — Advisory evaluators (after D)

**Goal**: the six advisories from §3 wired into `evaluate_all()`.

- Six new evaluators in `src/weatherbrief/advisories/evaluators/` following the existing registry pattern (see `designs/advisories.md`)
- Tri-axis sampling: for each route point × flight altitude (mapped to pressure level) × ETA → get Hewson field values (reuses `sample_hewson_at_route` from Phase A)
- Per-leg aggregation: max / mean / **P95** of each field across segment sample points (P95 not max — see §10a.3)
- Threshold logic per §3 table, refined with pilot feedback from Phase D telemetry
- Integration: new advisories show up alongside the existing 14 in the briefing

### Phase E — Moisture cross-check + stretch (future)

Opens after Phase D so the map + cross-section surface has something to show moisture *on*.

- **RH₉₂₅** derived from existing T/q — zero new fetch (§10a.1)
- **Low cloud cover (LCC)** + **total precipitation (TP)** fetched alongside existing pipeline variables — small delta
- **CAPE** for convection
- **Combined filter for Phase C advisories**: "Hewson signal ∧ moisture present" → real weather; "Hewson ∧ dry" → suppress advisory (or show as educational)
- **600 hPa** added if demand justifies
- **METAR-trend validation** track in the verification digest
- **Debrief feature** (issue #92) — pilot-owned dataset for real-time calibration
- **Surface θe** for convective outlook (needs 2 m T + Td)
- **wetter3.de archive integration** — replace live DWD download with archive pull when case `--date` is in the past

## 12. Status snapshot

| Item | Status |
|---|---|
| **Phase 1 + 2 (Hewson diagnostics + CLI)** | ✅ Done (PR #91, merged) |
| **Architecture consolidation (0.25°, ERA5 loader, unified Case)** | ✅ Done (PR #91, merged) |
| Resolution decision | ✅ 0.25° — consistent across all three models (ECMWF order at 0.25°, GFS/ICON ingestion at 0.25°) |
| Level decision | ✅ 925 / 850 / 700 hPa (3 levels; 500/400 explicitly rejected as upper-IFR out-of-scope) |
| Cadence decision | ✅ 2×/day — dedicated scheduler loop fires at 05 Z / 17 Z (~5 h after each 00/12 Z init, 1 h buffer before 06/18 Z full-cycle verification) |
| Storage decision | ✅ NPZ flat level-suffixed keys; ~24 MB total across 48h × 3 models |
| Retention decision | ✅ 48 h cache |
| ERA5 bulk fetch | ✅ Done (1 year, 2025-02 → 2026-02, ~700 MB on disk at `data/era5/hewson/`) |
| **Phase A** (route sampling) | ✅ Done (PR #93 open) — `sample_hewson_at_route`, `route-hewson` CLI, retrospective scripts |
| **Phase B.1** (multi-level Case storage) | ✅ Done (PR #94 open) — multi-level NPZ, back-compat, 10 tests |
| Phase B.2 (CLI `--levels`, rebuild Ciarán at 3 levels) | 🟡 Small follow-up; can slot anywhere |
| **Phase D.0** (precompute loop) | ✅ Done (2026-04-24) — `run_hewson_precompute_loop` + `weatherbrief.hewson.run_once()` + `python -m weatherbrief.hewson` CLI |
| **Phase D.1** (backend endpoints) | ✅ Done (2026-04-25, PR #96) — `/api/hewson-map`, `/api/hewson-map/manifest`, `/api/hewson-map/all-metrics`; admin-gated via `_synoptic_auth = require_admin` while calibrating |
| **Phase D.2** (map layer + tooltips + ERA5 cases) | ✅ Done (2026-04-25, PR #96) — Synoptic Forecast tab on `/maps.html`, canvas grid overlay, cursor-following tooltip with all 6 metrics, default/storm scale toggle, briefing-style (i) modal with Discuss-with-AI prompts, `era5-case` CLI for historical events (Storm Ciarán test case) |
| Phase D.3 (cross-section bands) | 🎯 **NEXT** — requires D.0 (done) |
| Phase D.4 (stencil in GRIB era) | Gated on native-GRIB ingestion being live for the user's briefing model |
| Phase C (advisory evaluators) | After Phase D, informed by what pilots actually use from the map/cross-section |
| Phase E (moisture cross-check) | After Phase D — RH₉₂₅, LCC, TP, CAPE, debrief feature #92 |
| Retrospective validation | ✅ Pairwise cancel test 3/3 (all pilot-cancellation days scored higher than replacement-flown days) — best calibration signal we have |

## 12a. Quick pickup from fresh context (for Phase D.3 start)

Phases D.0 / D.1 / D.2 are done and merged via PR #96. The next session picks up **D.3 — the cross-section bands** that overlay the same precomputed Hewson fields on the route cross-section canvas.

### What's already on main

- Hewson diagnostics math (`compute_hewson_diagnostics`) + `plot-hewson` CLI
- Multi-level Case storage (NPZ with level-suffixed keys)
- Route sampling library: `sample_hewson_at_route(case, model, waypoints, hours)` and `bilinear_sample` helper in `src/weatherbrief/frontal/route_sampling.py`
- `route-hewson` CLI subcommand that resolves ICAO codes and prints per-waypoint values
- Retrospective scripts: `scripts/analyze_flight_log.py` and `scripts/analyze_cancellations.py`
- 1 year of ERA5 GRIBs at `data/era5/hewson/` (gitignored)
- **Phase D.0**: `src/weatherbrief/hewson/` module — `precompute.py`, `cli.py`, `era5_case.py`, `__main__.py`. `run_hewson_precompute_loop` in `scheduler.py` at 05 Z / 17 Z. NPZ snapshots at `${DATA_DIR}/hewson/<model>/<init_iso_z>.npz`. Back-compat-preserving `levels=` / `level_hPa=` kwargs added to `fetch_grid_fields` and `reshape_to_fields` in `frontal/grid.py`.
- **Phase D.1**: `src/weatherbrief/api/hewson_map.py` — three endpoints (`/api/hewson-map`, `/manifest`, `/all-metrics`). Lazy NPZ access, corrupt-file → 404, path-traversal guard, `Cache-Control: private, max-age=86400, immutable`. Admin-gated via `_synoptic_auth` alias.
- **Phase D.2**: Synoptic Forecast tab on `/maps.html`. `web/ts/visualization/synoptic-map.ts` + `hewson-grid-layer.ts` (canvas overlay) + `hewson-colormaps.ts` (matplotlib-equivalent ramps with default/storm scale) + `hewson-metrics-catalog.ts` (per-level pilot-facing thresholds) + `hewson-info.ts` (briefing-style modal). Cursor-following tooltip reads `/all-metrics` cached per (model, init, level, hour). Progressive load: single-metric slice first, all-metrics in the background. Token-based stale-fetch cancellation.
- **ERA5 historical cases**: `python -m weatherbrief.hewson era5-case --case <dir>` builds a synoptic snapshot from any calibration Case directory; surfaces in the same UI under model="era5". Storm Ciarán (2023-11-02) is the test case.

### What Phase D.3 needs

Cross-section bands per § 7.9. Sample the precompute snapshot at each waypoint × {925, 850, 700} via bilinear, draw 3 horizontal coloured bands at 2,500 / 5,000 / 10,000 ft on the route cross-section canvas. Reuse the same colormap and metric picker as the map.

**Entry points to find first**:
- `web/ts/visualization/cross-section/renderer.ts` + `layer-registry.ts` — the existing layer pattern to follow (cloud / icing / CAT / inversion are sibling layers)
- `src/weatherbrief/frontal/route_sampling.py::bilinear_sample` — already implemented; reuse for the per-waypoint × per-level lookup
- `web/ts/visualization/hewson-colormaps.ts` — colormap and (vmin, vmax) ranges to reuse
- `web/ts/data/hewson-metrics-catalog.ts` — pilot info for the cross-section's (i) modal
- This PR's `web/ts/maps-main.ts` — for the metric/scale-picker UI patterns to mirror in the cross-section controls

### Open questions deferred from D.1/D.2

- ✅ Serialization: started with JSON. ~80 KB single metric, ~2.3 MB for all-metrics. Acceptable on local dev; would benefit from FastAPI `GZipMiddleware` (~250 KB compressed) for slow connections. Not in PR #96; easy follow-up.
- ✅ Map client: single gridded CanvasLayer with redraw on metric change — chosen and shipped.
- ✅ Pilot tooltip text: inline catalog (`hewson-metrics-catalog.ts`) — chosen and shipped.
- Cross-section: 3 discrete bands (D.3 / Open-Meteo era) → continuous-altitude stencil (D.4 / GRIB era) — silent data-source upgrade when native GRIB ingestion lands.

## 13. Related docs

- `designs/frontal-detection.md` — zone-scale detection work this builds on
- `designs/future/frontal-detection-plan.md` — original plan (deliberately simpler than Hewson)
- `designs/future/front-calibration.md` — calibration workflow and scoring
- `designs/advisories.md` — where the new advisories plug in
- `designs/analysis-metrics.md` — where these fields appear in the metric catalog
- `designs/forecast-page.md` — where the new map layer lives
- `designs/metar-taf-accuracy.md` — validation infrastructure we'll reuse
- `designs/fetch.md` — grid-fetch and caching patterns
