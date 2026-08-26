# Current Conditions — measurements, decisions, and integration notes

> Companion to `current-conditions.md`. Verification of that doc's claims, live
> measurements against real data, and the decisions taken from them.
> All decisions dated **2026-08-25** unless noted. Nothing implemented yet.

The parent doc proposes the feature. This doc records what survived contact with
the real APIs, the real files, and the real codebase — and what was decided as a
result. Where the two disagree, this one is current.

---

## 1. Decisions

| # | Decision | Detail | § |
|---|---|---|---|
| D1 | **Phase 1 is observation display, not comparison** | All sources shown; model comparison is phase 2 | §6 |
| D2 | **Severity is permanently annotate-only** | The cross-check never moves a hazard grade — not "until calibrated" | §1.1 |
| D3 | **Two sibling cross-section layers** | `observed-tops` (group `clouds`, **default ON**), `observed-surface` (group `conditions`) | §1.2 |
| D4 | **Inline in `briefing.json`** | Beside `route_observations`; imagery never in JSON | §1.3 |
| D5 | **Collector on the droplet** | `scheduler.py`, mirroring `run_metar_ingest_loop` | §1.4 |
| D6 | **`h5py` only — no GDAL/rasterio** | Read ODIM `.h5`; reproject with `pyproj` + numpy | §1.5 |
| D7 | **Retention in hours, per-source** | LI/DBZH/RATE 3 h, CTTH 1 h — not a 24-hour cache | §1.6 |
| D8 | **Two verdict axes** (phase 2) | `echo_match` from DBZH, `intensity_match` from RATE | §1.7 |
| D9 | **Tops comparison is per-model** (phase 2) | Not `wp_forecasts[0]` | §1.8 |
| D10 | **CTTH at full 10-min cadence** | Downsampling premise removed by D7 | §1.9 |
| D11 | **Data-absence is a first-class three-state** | Per source; never conflate "no data" with "nothing there" | §1.10 |
| D12 | **Attribution from each frame's own metadata** | `how/license` is machine-readable | §1.11 |

### 1.1 Severity: permanently annotate-only (D2)

The codebase already has this contract by name — `convective_character.py`
("It never changes the severity advisory"), `models/advisories.py` (mitigations:
"never changes the advisory's grade"), `models/analysis.py` ("never changes the
severity advisory's colour"). **Adopt it by name, and treat it as final.**

The reason is structural, not provisional: an advisory grades **the flight, at
ETA**; an observation describes **now**. Letting a now-observation move an at-ETA
grade collapses the two axes this feature exists to keep apart. METAR — longer in
the product and better understood — does not move severity either.

What calibration should unlock instead is **confidence, not hazard**:
`model_agreement` / `dd_nwp_agreement` already grade model trustworthiness, and an
observed disagreement is direct evidence about exactly that. The question to
calibrate is therefore *"may it feed model-quality?"*, and the evidence needed is a
hit/false-alarm rate on the disagreement verdict measured over a season.

### 1.2 Layers (D3)

Two new layers; the existing `current-conditions` layer (METAR columns + SIGMET
zones) is untouched.

- `observed-tops` — group **`clouds`**, `defaultEnabled: **true**`
- `observed-surface` (radar ribbon + flash glyphs) — group `conditions`, default off

Sibling rather than extension, for three reasons:

1. **Independent toggles** — folding four sources into one render call means a
   pilot cannot keep the tops band while turning off the radar ribbon.
2. **One `metricId` per layer** — it drives the (i) help button, and "observed vs
   NWP tops" and "observed precipitation" are different explanations.
3. **Different stack positions** — the tops band must render *with* the cloud
   bands for the comparison to read; the ribbon must sit at the bottom near
   terrain. A layer occupies one position in `ALL_LAYERS`.

`observed-tops` goes in the `clouds` group deliberately: that is where a pilot
looks for a cloud-top control, beside the NWP layers it contradicts. Default-on is
safe because `data-extract.ts` already grays out layers with absent data
(`unavailable.add(...)`), and in phase 1 **this layer is the cross-check
mechanism** — the pilot's eye does what phase 2 will compute.

Name the Viz field **`observed`**. `currentConditions` is taken.

### 1.3 Payload placement (D4)

Inline on `briefing.json`, exactly like `route_observations`.

Measured pack: `briefing.json` **300 KB**, `forecasts.json` 1.5 MB,
`cross_section.json` 6.7 MB. The observed block is ~40–60 KB — **+15–20% on the
smallest artifact in an ~8 MB pack**.

| | inline | sidecar |
|---|---|---|
| Realtime refresh | free — `run_realtime_refresh` already loads/patches/writes it | new write path |
| iOS offline bundle | free — whitelist already maps `"snapshot": ["briefing.json"]` | new whitelist entry |
| PDF export | free — template already renders a `route_observations` sibling | new plumbing |

The sidecar's only argument — that realtime refresh rewrites the file — does not
survive 300 KB → 360 KB every five minutes. **Split by kind, not by convenience:
numbers inline, imagery never in any JSON.**

### 1.4 Collector location (D5)

Droplet, in `scheduler.py`, mirroring `run_metar_ingest_loop` (which already runs
an observation ingest on a wallclock boundary via
`_seconds_until_next_30min_boundary`). The radar loop is that with a 5-minute
boundary.

**Not the M4 mini.** The offload path suits the heavy scheduled standalone cycle
and not a live D-0 feature: the mini is a home-LAN machine on DHCP with no VPN
reachability that has already silently dropped out of a deploy once.

### 1.5 Decoder (D6)

Add `h5py`; **no GDAL, no rasterio**. Read `.h5` (2.7 MB) not the COG (4.4 MB).
Map reprojection is `pyproj` (already a dependency) + numpy nearest-neighbour —
and nearest-neighbour is the meteorologically correct choice anyway, since
bilinear resampling of reflectivity smears peak dBZ.

Also declare `eumdac` and `netCDF4`, currently installed in the dev venv but in no
manifest.

### 1.6 Retention (D7)

Per-source, driven by what each source's own metrics need:

| Source | Retention | Why | Disk |
|---|---|---|---:|
| LI flashes | 3 h | the only rolling-window metrics (`flashes_10m/30m/60m`) need ≥60 min | ~7 MB |
| DBZH | 3 h | newest + recovery margin; no history metric | ~90 MB |
| RATE | 3 h | same | ~18 MB |
| CTTH | 1 h | tops are instantaneous — nothing reads past the newest | ~324 MB |
| | | | **~440 MB** |

Against ~9 GB/day for full-day retention, of which CTTH alone is 74%. Nothing is
lost: OPERA's open S3 cache is 24 h and EUMETSAT's archive is deeper, so
re-derivation by product ID stays possible within a day — the same bound that
applied regardless.

### 1.7 Verdict axes — phase 2 (D8)

DBZH and RATE each emit their own verdict. They answer different questions, so
they are not two opinions on one axis:

| Axis | Source | Question | Cadence / latency |
|---|---|---|---|
| `echo_match` | DBZH | *Is there precipitation here at all?* | 5 min / ~4 min |
| `intensity_match` | RATE | *How intense, in units the model can be checked against?* | 15 min / ~10 min |

This mirrors the two claims `enroute_precip` actually makes — phase **and**
intensity — and follows the validated convective two-axis pattern (severity +
character).

Rules that keep it legible: **never two `category_match` fields** (distinct names,
distinct questions); **each verdict carries its own frame's valid time and age**
(two ages on screen is the parent doc's own no-synthetic-timestamp rule applied
honestly); and **RATE is the only source allowed to claim an intensity** — we never
apply our own Z–R conversion, which would invent a proxy chain inside the tool
built to check proxy chains.

Phase 1 collects both products even though neither emits a verdict yet.

### 1.8 Per-model comparison — phase 2 (D9)

Compare observed tops against **every model** that has a cloud top at that station.

- Matches `cloud_top`, which already grades per model and aggregates worst/majority.
- Only this variant can answer *"which model gets tops right"* — the calibration
  payoff, and the evidence that would feed `model_agreement` under D2.
- Only **comparisons** multiply; observed values are model-independent by
  construction, so `stations[]` does not grow.
- Explicitly rejects inheriting `run_observation_comparison`'s `wp_forecasts[0]`,
  which the code itself treats as a rough choice.

**UI rule:** never six verdicts inline. Aggregate + rendered model's verdict
visible, per-model split on drill-in — the hook-then-detail shape the convective
per-model/cross-check split already uses.

### 1.9 CTTH cadence (D10)

Take every frame at 10 min. The case for downsampling was storage (8.2 GB/day);
D7 removes that premise — at 1-hour retention CTTH is ~324 MB regardless of
cadence. What remains is ~36 min/day of background download and ~9 s/day of
decode, and convective tops can grow materially inside 20 minutes.

### 1.10 Data absence is a three-state, per source (D11)

The single most important correctness rule in the feature, and it differs by
source.

**Radar** — three distinct outcomes, and 49.4% of the grid is the first one:

| State | Meaning |
|---|---|
| `nodata` | **no radar coverage** — absence of evidence |
| `undetect` | radar looked, **saw nothing** — a real negative observation |
| value | echo of measured intensity |

**Satellite** — the same split exists and is *better* served, because a
geostationary disc has essentially no coverage gaps over Europe:

| `quality_method` | Meaning | Route-window share |
|---:|---|---:|
| 0 | **no cloud — positive observation of clear sky** | 41.9% |
| 1 | opaque IR, low stratus / fog | 27.6% |
| 6 | opaque cold cloud — Cb / thick cirrus | 23.4% |
| 7, 8 | thin cirrus / semi-transparent high | 3.2% |
| 9 | **multi-layer suspect** | 3.7% |
| 10 | other semi-transparent, no height retrieved | 0.1% |

So a satellite *"no high cloud here"* is trustworthy in a way a radar *"no echo
here"* is not. Encode that asymmetry rather than treating the sources alike.

Carry `quality_method` as a **small histogram per annulus**, not one
"high-confidence" count: qm=9 is precisely the flag saying a layer may lurk under
the retrieved top, which is the caveat the "can I get on top?" question needs.

**Lightning** has a third variant of the same problem: MTG-I1 sits at 0°, so
detection efficiency degrades northward. A raw flash count is **not comparable
across latitude** — "0 flashes" over Scotland is weaker evidence than over Spain.

Consequences that must be built, not bolted on:

- Every summary comment must emit **"no radar coverage here"** as an outcome
  distinct from **"radar looked and saw nothing"**.
- The route-graph series needs a visible **no-coverage state** — not a gap, which
  reads as zero.
- The map must distinguish uncovered from echo-free area, or a clipped raster
  implies an all-clear it cannot support.
- Persist `valid_px` and coverage fraction with **every** value, and refuse to
  grade below a coverage floor.

This matters *more* in phase 1 than phase 2: without a comparison to carry
confidence, every phase-1 statement is a bare assertion of observed fact.

### 1.11 Attribution (D12)

Three surfaces, all with existing mechanisms:

1. **Map overlay** — reuse the per-source `attributionHtml` that `synoptic-map.ts`
   already uses for chart basemaps. This is the one CC BY strictly requires:
   attribution travels with the material.
2. **Help / Data Sources table** — `help.html` renders `data-sources-table-host`
   live from the source registry, whose `SourceConfig` already carries
   `provider_label`. Registering observation sources there needs the
   interval-schedule extension (§4.3) — **the freshness gap and the attribution gap
   have one shared fix.**
3. **PDF** — one line in the observed section, beside the METAR/TAF block the
   template already renders.

Not a global footer: CC BY wants attribution with the material, and a footer is
weaker and noisier.

**Take the values from the data.** Every ODIM frame carries
`how/license = ".../by/4.0/ (CC BY 4.0)"` plus `creator_name` / `publisher_name`,
and the producer varies — the sampled frame was built by **Météo-France**, not
EUMETNET centrally, so hardcoding would have been wrong.

---

## 2. Measured evidence

Everything in this section was run against live APIs and real files, not estimated.

### 2.1 Access re-tests

| Claim in the parent doc | Result |
|---|---|
| Credentials present in `.env` | ✅ all three |
| EUMETSAT token + collection search | ✅ CTTH `0681`, LI `0691`, AFA `0687`, OCA `0684` all searchable |
| MeteoGate keyed access | ✅ `/collections` → 200 |
| Anonymous S3 cache readable | ✅ and keys are **deterministic** |
| DBZH 5-min, RATE 15-min | ✅ 182 vs 61 objects in 15 h |
| Radar 2–7 min old | ✅ DBZH@1505Z written 15:09:20Z → **4 min** |
| LI < 1 min after period end | ✅ **20 s** |
| CTTH ~11 min after period end | ✅ **10.4 min** |
| Radar cache ≈ 0.9 GB/day | ✅ for DBZH+RATE; ~1.0–1.15 GB/day including ACRR |

**Better than documented — the S3 keys are derivable:**

```
https://s3.waw3-1.cloudferro.com/openradar-24h/
    YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@{DBZH,RATE,ACRR}.{h5,tiff}
```

**Always name the endpoint, never just the `s3://` URI.** The ORD cache is
hosted by CloudFerro, not AWS — `openradar-24h.s3.amazonaws.com` answers
`NoSuchBucket`. An earlier revision of this doc gave the bucket without the
host; the implementation read it as AWS, and because a 404 is deliberately
treated as "not published yet", the whole radar path collected nothing and
logged nothing until it was caught by hand on first run.

Flat, immutable, no catalogue query, no credentials. The collector is a poller, not
an MQTT client — **demote MQTT to a later phase**, since it saves seconds of latency
in exchange for a long-lived connection with its own reconnect/dedup/gap-recovery
failure modes.

**Worse than documented:**

- **Anonymous MeteoGate returned 503**, keyed worked. Do not build a fallback on it.
- `/collections/observations/items` **requires `bbox` or `platform`** — a plain time
  query is rejected.
- **RATE latency is ~10 min, not 2–7.** Only DBZH is in that band. Track cadence and
  latency per quantity.
- **COG is 1.7× larger** than ODIM (4.4 MB vs 2.7 MB per DBZH frame).

**Also accessible:** OCA (`0684`) returns upper *and* lower cloud layers per pixel —
directly addressing the multilayer limitation the parent doc flags, and recorded in
`satellite-cloud-top-validation.md` as never fetched. FCI L1c (`0662`) now returns
**search results** where that doc recorded a 403; download may still be gated, but
its status note should be re-tested.

### 2.2 Radar, end to end

`OPERA@20260825T1250@0@DBZH.h5`, LFMD→EGTF (~560 NM), 200 stations × 3 annuli.

| Step | Cost | Note |
|---|---:|---|
| S3 download (2.0 MB) | **~4.7 s** | ~1.7 MB/s |
| Full-grid read | 158 ms | 4400×3800 **float64 = 134 MB** |
| **Route-bbox windowed read** | **8.3 ms** | 994×563 = **4 MB** |
| **Corridor sample, 200 × 3** | **33.9 ms** | |
| **Per-briefing total (frame local)** | **~42 ms** | |

Gzip-chunked in **(760, 880)** blocks, so a route bbox decompresses only overlapping
chunks. **Never read `[:]`** — the payload is float64, so data + quality naively is
268 MB, not the ~33 MB an int16 assumption suggests.

**Georeference validated:** all four grid corners round-trip through `pyproj` to
**5 decimal places** against the file's own `LL/LR/UL/UR` attributes. `where/projdef`
is a plain PROJ string:
`+proj=laea +lat_0=55 +lon_0=10 +x_0=1950000 +y_0=-2100000 +units=m +ellps=WGS84`.

**Pixel census** over 16.7 M cells: `nodata` **49.4%**, `undetect` 44.4%, real echo
6.2% (−32…77 dBZ). See §1.10.

### 2.3 Satellite (CTTH), end to end

`MTI1+FCI-2-CTTH`, sensing 13:00–13:10Z, same route and stations.

| Step | Cost |
|---|---:|
| Download (71.5 MB zip) | **14.9 s** @ 4.8 MB/s |
| Windowed read, 5 variables | ~23 ms warm / ~55 ms cold |
| Inverse geos projection of window | 27 ms |
| **Parallax correction (103k px at once)** | **0.6 ms** |
| Build `cKDTree` (103k points) | 19 ms |
| Query 200 stations × 3 annuli | 19 ms |
| **Total, frame local** | **~90–130 ms** |

**Vectorising is worth ~1000×.** The research scripts sample per airport, and the
parent doc measured 0.5–0.8 s for *two* airports — extrapolated to 200 stations that
is **100–160 s**, which would wreck a briefing. Read the window once, correct
parallax as one array op, build one tree, query all stations against it: **0.09 s**.
**Per-station work must never touch the file.**

**Chunking: rows matter, columns do not.** `[23, 5568]` full-width row strips.
Measured cold, fresh handle each time:

| Read | Cost | Returned |
|---|---:|---:|
| 399 rows × 445 cols | 10.3 ms | 0.4 MB |
| 399 rows × **5568** cols | 11.0 ms | 4.4 MB |

Identical — narrowing columns buys nothing. Compute a tight **row** range and do not
optimise columns. This also means keeping the raw newest frame and reading windows
per briefing is simpler than maintaining a cropped derivative.

**Georeference validated** across four airports (airport → scan angle → pixel index
→ back to lon/lat): LFPG 635 m, LFMD 1022 m, EDDM 1421 m, EGTF 1970 m — all
**sub-pixel** against a ~2 km pixel. `+proj=geos +sweep=y` is correct; no Satpy
needed.

**Parallax is larger than the corridor.** Over 103k valid route-window pixels:
median **52.3 km**, p90 58.0 km, max **63.6 km**; for tops above 8000 m, median
56.8 km. At 2 km/pixel that is **26–32 pixels**.

> The 20 NM corridor is **37 km** wide. The parallax shift is **52 km**.

An uncorrected sample is not slightly off — it reads a location entirely outside the
corridor it claims to describe. Two consequences: the **read window must be padded
by at least the maximum shift (~65 km)**, or pixels whose corrected position lands in
the corridor were excluded before correction ran (the parent doc's "start with 100 km
and test" is validated); and the regression fixture must fail if parallax is removed,
which is easy to make sharp since removing it changes essentially every value.

### 2.4 Per-route vs shared index

| | Window | Prep | Query | Total |
|---|---|---:|---:|---:|
| Route bbox, 200 stations | 351×402 | 54 ms | 4.1 ms | **58 ms** |
| Europe-wide, 620 airports | 931×1833 | 456 ms | 10.7 ms | **467 ms** |

Vectorising works *within* a set of stations sharing one window, not across routes.
Per-route redoes window + projection + tree for 58 ms, which is nothing. The
620-airport standalone case uses the opposite pattern — one window, one tree, all
620 queried at once (0.47 s, versus ~36 s done individually).

**One primitive, two call sites**: `sample(frame, window, stations, radii)`.

A shared per-frame Europe index would cut per-route cost to 4 ms, with **break-even
at ~8.5 briefings inside one 10-minute frame**. Not worth building now, and a
drop-in later because the primitive already takes a window. Note the Europe prep is
dominated by projection (257 ms) and tree build (174 ms) — pure CPU, so perhaps
2–3× slower on the droplet.

### 2.5 What the timings mean

Radar ~42 ms and tops ~90–130 ms are both **noise inside a briefing that takes
minutes**. Sampling runs inline with no gating and no async plumbing.

**Download dominates in both cases** — 4.7 s radar, 14.9 s CTTH, roughly 100× the
analysis. That is the entire case for the frame collector: with a local frame the
briefing pays ~42 ms; fetching inline it pays seconds.

Droplet timings are unmeasured. Expect 2–3× on CPU-bound steps; re-measure before
optimising anything.

---

## 3. Design rules that follow from the measurements

- **The corridor is per-station, not per-route.** `ROUTE_GRAPH_METRICS` entries read
  `getValue: (p) => p.field` off a `VizPoint`, so observed values are needed at every
  station. Compute all three annuli × every station in one pass and ship them
  together — the **corridor selector is then a pure client-side pick** with no
  re-fetch, and the cross-section band and route-graph line stay in sync because
  they read the same selection. ~100 stations × 3 × 12 fields is trivial.
- **Parallax before corridor membership**, always, with a ≥65 km window pad (§2.3).
- **Never per-station file access** (§2.3).
- **Never read a full grid** (§2.2, §2.3).
- **Data absence is a three-state per source** (§1.10).
- **Route-graph paired axis** — there is already a `precipitation` metric, the
  *model's*. An observed series must share its **axis and scale**. The dual-Y-axis
  design exists to plot two *different* quantities; using it for forecast-vs-observed
  of the same quantity at independent scales makes agreement and disagreement look
  identical. Needs a small paired/overlay render type, not two registry entries.
- **Nothing here has a vertical profile.** Radar is a column max (`product = MAX`),
  lightning is a point, CTTH is a top surface. Any rendering that implies vertical
  structure is a lie. Tops → a band; radar → a ground-anchored ribbon that never
  extends upward; lightning → glyphs on that ribbon; a full hatched column **only**
  for a fused object (≥45 dBZ + flashes + cold top).
- **Latency is a product-safety matter, not a caption.** DBZH is a rolling 10-minute
  maximum (`starttime=124001`, `endtime=125000` for a frame labelled `1250`),
  delivered ~4 min later and rendered a minute after that — so an on-screen echo can
  be **~15 min old**, ~30 NM of own-ship at 120 kt plus whatever the cell did. Put an
  age-in-minutes badge **on the layer itself**, age-fade past ~15 min, and never draw
  corridor bands so they read as "this path is clear".

---

## 4. Integration seams in the existing code

### 4.1 The name `current-conditions` is already taken

`web/ts/visualization/cross-section/layers/current-conditions.ts` is a shipped
layer — *"Current conditions overlay (D-0): METAR airport columns + route SIGMET
zones"* — and `VizData.currentConditions` is already built by
`buildCurrentConditions()` in `data-extract.ts`. Hence D3's `observed` naming.

### 4.2 The pipeline seam, and the one that matters for in-flight

`pipeline.py` §3.5 gates on `days_out == 0 and options.airports_db_path and not
options.historical_mode`, calls `run_route_weather()` then `run_route_sigmets()`,
each in its own `try/except`, attaching both **inside `briefing.json`**.

The in-flight path is a different function: **`run_realtime_refresh()`** in
`tasks/route_weather.py`, called from `api/packs.py` in three places (the ↻ button
and the tiered gate's `realtime` mode). It reads the pack off disk, re-fetches, and
patches `briefing.json` in place — no model fetch, no GRIB, no LLM. **That is the
seam for "within a few hours or during the flight window."**

### 4.3 The freshness registry does not fit an observation stream

`fetch/freshness/registry.py` keys `SourceConfig` by `{model}:{source}` with
`cycles` (UTC hours-of-day), `delivery_offset` and a forecast `horizon`. A
5-minute observation stream has none of those. Either add an interval-schedule kind
to the dataclass or keep observation freshness in its own store. Required for both
the freshness display and D12's attribution table.

### 4.4 The comparison vocabulary already exists (phase 2)

`ObservationComparison` grades each METAR axis **`CONFIRMING` / `SIGNIFICANT` /
`CONFLICTING`**, carries signed deltas and a `detail` string, and rolls up to
`RouteObservations.has_conflicts`. Mirror it — including the container shape
(`corridor_nm`, `fetch_time`, coverage counts, item list, `comparisons`, `worst_*`,
`has_conflicts`).

### 4.5 Two advisories already grade what we would observe (phase 2)

The strongest argument for the feature, and absent from the parent doc:

- **`advisories/cloud_top.py`** grades *"can the pilot get on top?"* from a
  **modelled** cloud top, on every briefing, with **no observational check
  whatsoever**. CTTH is direct truth for that number.
- **`advisories/enroute_precip.py`** states in its own docstring: *"No model
  provides visibility at altitude … What every model does deliver is hourly
  precipitation."* An admitted proxy chain — radar validates its first link,
  **between airports**, exactly where METAR is blind.
- **`advisories/convective.py`** — LI flash rate is the observational counterpart.

### 4.6 The time-alignment fork (phase 2)

`run_observation_comparison` compares the METAR against the model at
`_interpolate_airport_time(...)` — the airport's **ETA**, not the METAR's own time.
For briefing that is right: *"does what's happening now resemble what the model
expects when I get there?"*, which is why the verdicts are soft words.

It is **wrong for calibration**. Two consumers, two alignments, and they must not
share a code path:

| Consumer | Observed at | Model sampled at |
|---|---|---|
| Briefing display | now | station ETA |
| Verification row | T | valid time T, issue and lead time immutable |

`at_time()` **clamps silently** — the verification path must refuse rather than
clamp, or it scores a forecast hour that was never decoded.

**Phase 1 avoids this entirely**, because it samples no model.

---

## 5. Corrections to the parent doc

1. **The 24-hour cache section needs a rewrite, not a trim.** Manifest, checksum,
   atomic-write and prune-after-newest-valid mechanics all survive; the 7.9 GB/day
   sizing and the 24-hour framing do not, once the slider and nowcasting are out
   (D7).
2. **MQTT is not required** — S3 keys are deterministic (§2.1).
3. **Anonymous MeteoGate is unreliable**; `items` requires `bbox`/`platform` (§2.1).
4. **RATE latency ~10 min**, not 2–7 (§2.1).
5. **No decode dependency exists today** — `h5py`, `rasterio`, `boto3`, GDAL all
   absent; `eumdac`, `satpy`, `netCDF4` installed but undeclared (D6).
6. **OPERA COMP has no echo-top product** — DBZH/RATE/ACRR only. All vertical
   information comes from CTTH/OCA.
7. **CTTH variables are `uint16`/`int8`, not float32**, and the netCDF is 53.7 MB
   (71.5 MB zipped), not ~95 MB.
8. **"Read the bounding window" is a hard requirement, not an optimisation** — and
   for CTTH only the *row* range matters (§2.3).
9. **Lightning detection efficiency degrades northward** — unmentioned, and it would
   silently corrupt any pan-European flash calibration (§1.10).
10. **`current_conditions.json` collides** with a shipped layer id (§4.1).

---

## 6. Phase plan

### Phase 1 — Observe and show

- **Collector**: DBZH (5 min), RATE (15 min), LI (10 min), CTTH (10 min).
- **Sampler**: per-station × {5, 10, 20} NM, all sources, one primitive.
- **`ObservedConditions`** inline on `briefing.json`; refreshed via
  `run_realtime_refresh`.
- **Cross-section**: `observed-tops` (default ON) + `observed-surface`.
- **Map**: corridor buffers, newest frame clipped to 20 NM, lightning points, age
  badge. No animation, no tile server, no `ChartCache` reprojection — a single
  `imageOverlay` on the route bbox.
- **Route graph**: observed precip rate + flash rate, corridor selector.
- **Deterministic "Observed now" summary** — briefing section, PDF, digest context.
  No LLM.
- **Attribution** from each frame's `how/license`.
- **Coverage discipline throughout** (§1.10) — the defining correctness requirement
  of this phase.

Explicitly **not** in phase 1: any model sampling, any verdict, any advisory
wiring, the ETA-vs-obs alignment fork.

### Phase 2 — Compare

`echo_match` / `intensity_match` / tops verdicts, per-model (D8, D9); the
time-alignment fork (§4.6); annotate-only notes on `enroute_precip` / `cloud_top` /
`convective`; the iOS `GET /api/flights/{id}/observed` endpoint.

### Verification collection

Writing observed rows needs **no forecast alignment** — that belongs to scoring. So:

- **C1 — write observed rows at ingest.** Same sampler, pointed at the standalone
  ~620-airport window. This is the half the backfill argument applies to: neither
  migration 092's convective ingredient columns nor OPERA's 24-hour cache can be
  recovered later. Cheap enough to ride along with phase 1.
- **C2 — score against forecasts.** Phase 2, with the verdicts.

**Why C matters beyond this feature:** today the whole cloud/icing stack is verified
against METAR, which gives cloud **base**, at airports only. CTTH gives cloud
**top**, everywhere — the quantity the cross-section draws on every briefing and
that nothing currently checks. Radar gives precipitation **between** airports. And
observed DBZH is the natural truth for ICON-D2's explicit reflectivity, which is the
other half of the pairing **#442** needs.

### iOS

Inline placement (D4) means the offline bundle carries the observed block
automatically. **Carry it stamped and aged**, not omitted — a last-known frame with
its valid time beats a hole, provided the client ages it out (gray/strike radar past
~30 min). Live in-flight is not pack sync's job; the dedicated endpoint is phase 2.
No raster on iOS in v1.

---

## 7. Open items

1. **Confirm the droplet's inbound transfer is unmetered.** DigitalOcean is believed
   to meter egress only; at 10-min CTTH cadence this is ~246 GB/month inbound, which
   would matter if that belief is wrong. The only unverified assumption in the plan.
2. **Measure on the droplet** before optimising anything (§2.5).
3. **Coverage floor value** — below what fraction does a station refuse to report
   rather than assert? Start conservative, calibrate later.
4. Whether C1 ships with phase 1 (recommended) or waits.
5. Whether to evaluate **OCA** in place of CTTH for multilayer (§2.1).

---

## 8. References

- `designs/future/current-conditions.md` — the proposal this verifies
- `designs/future/satellite-cloud-top-validation.md` — CTTH decoder, `quality_method`
  code table, earlier route experiments
- `designs/metar-taf-route-weather.md` — D-0 pipeline and realtime refresh seam
- `designs/metar-taf-accuracy.md` — verification ingestion and tiered retention
- `designs/freshness-markers.md`, `designs/time-alignment-audit.md`
- EUMETNET ORD: <https://eumetnet.github.io/openradardata-documentation/>
