# Current Conditions — Observed Radar, Lightning and Cloud Tops

> Phase 1 of #574. **Displays observations only** — it computes no verdict and
> touches no advisory.

Shows remotely observed conditions at their source times, next to what
the models forecast: OPERA radar reflectivity and rain rate, EUMETSAT MTG
total lightning, and EUMETSAT MTG satellite cloud tops.

The cross-check still happens — `observed-tops` renders directly over the NWP
cloud bands, with geometric heights explicitly labelled in feet MSL and any
pressure flight levels kept separate.
**Computing** that comparison is phase 2 (see [Out of scope](#out-of-scope)).

> **Note on provenance.** This was written before
> `designs/future/current-conditions.md` and
> `designs/future/current-conditions-review.md` were on any branch — issue #574
> cited them, but neither file (nor the commit `af6dafae` it named) existed here
> at the time. The source material was the issue body, which carries the
> measurements and the non-negotiables, plus
> `designs/future/satellite-cloud-top-validation.md`.
>
> Both docs landed on `main` in `8c06ebb2` while this branch was open, and the
> implementation was checked against them after merging. **Decisions D1–D12 all
> hold as built** — including the ones most easily got wrong: the two sibling
> layers and their groups and defaults (D3), inline placement on `briefing.json`
> (D4), the collector on the droplet mirroring `run_metar_ingest_loop` (D5),
> `h5py` with no GDAL or rasterio (D6), per-source retention in hours (D7), CTTH
> at the full 10-minute cadence (D10), three-state absence per source (D11), and
> attribution read from each frame's own `how/license` (D12). D8, D9 and the
> time-alignment fork are phase 2 and untouched here, as that plan intends.
>
> Two display rules from §3 of the review doc are **not** implemented as written,
> and are recorded here rather than quietly skipped:
>
> - **Paired route-graph axis.** The rule asks for observed precipitation to
>   share the *model* `precipitation` metric's axis and scale via a paired
>   render type, on the grounds that independent scales make agreement and
>   disagreement look alike. This ships two ordinary registry entries instead;
>   the paired render type does not exist yet. The two also carry genuinely
>   different units — the model metric is accumulation (mm), the observed one a
>   rate (mm/h) — so a shared scale needs that reconciled first.
> - **Age-fade past ~15 minutes.** The cross-section layers badge the frame's age
>   but do not fade with it. The map's lightning trail does fade; the radar and
>   tops layers do not.
>
> The follow-up [PR #584 review](reviews/2026-09-05-pr584-observed-review.md)
> found correctness gaps in stored ages and coverage wording. The corrective
> branch updates clock-driven per-source labels; age-fading remains a separate
> visual choice, not a substitute for accurate timestamps.

## Why this shape

Four decisions carry the design. Each exists because the obvious alternative
is actively wrong, not merely worse.

### 1. Data absence is three-state, per source

**49.4% of the OPERA grid is `nodata`** — sea, mountain shadow, and everything
beyond any member radar's range. ODIM distinguishes that from `undetect`,
which means a radar looked at the pixel and found nothing.

Both decode to "no value". Conflating them turns *we cannot see there* into
*it is clear there*, over half of Europe. So the pixel counts travel with
every value, all the way to the client:

```
total_px == valid_px + nodata_px
valid_px == detected_px + undetect_px
```

`insufficient_coverage` (below `MIN_COVERAGE_FRACTION`, 0.35) prevents an
absence claim; it does not erase measured positive detections. Render values
alongside the distinct coverage hatch/warning. A gap alone can read as "no rain".

On the satellite side the same discipline applies with different vocabulary:
`quality_method == 0` includes both cloud-free and unprocessed pixels. Only
explicit cloud-free status, without contradictory retrieval values, establishes
`undetect`. Failed, unknown and off-disc retrievals are `nodata`.

Lightning is a point product and `ObservedFlashAnnulus` currently has no coverage
split. LI does not cover the entire disc; zero means no flashes reported in the
specified window, not verified full coverage or absence of convection.

This matters *more* in phase 1 than it will in phase 2, because no comparison
exists yet to carry confidence.

### 2. Parallax before corridor membership

The satellite sits over the equator. Its line of sight to a cloud at 50°N
continues past the cloud and strikes the ground *north* of it, so the pixel
containing a cloud-top claims a ground position tens of kilometres away.

Earlier research reported a 52 km median displacement against a 37 km (20 NM)
corridor. Its unusually large low-cloud offsets need independent real-granule
validation; the synthetic tests establish correction application, not its scale.

The product ships per-pixel `delta_latitude` / `delta_longitude`; the sampler
applies them *before* deciding which pixels belong to a station, and the read
window is padded so the pixels it is about to look for are inside the block
that was read. A pad smaller than the displacement silently truncates the
high-cloud tail — exactly the signal the "can I get on top?" question depends
on.

**The pad scales with the viewing geometry — latitude *and* longitude.** The
75 km figure (`PARALLAX_PAD_KM`) was measured at 50°N near the 0° meridian, and
displacement grows with the satellite zenith angle, which depends on the
great-circle angle from the sub-satellite point: `cos(psi) = cos(lat)·cos(dlon)`.
Latitude alone under-reads it everywhere off the meridian, which is most of
Europe — Warsaw was padded 82 km where it needs 94, Riga 102 against 120,
Helsinki 121 against 144, while western Europe was unaffected only because the
75 km floor absorbed the error. `parallax_pad_km(lats, lons)` takes the
worst-viewed point in the set, scales by the zenith-tangent ratio, and clamps at
a 70° sub-satellite angle, where the geometry degenerates and the retrieval is
unusable anyway. It is the geometry's *ratio* that is used, never its absolute
value. The earlier factor-of-four discrepancy with `h × tan(zenith)` remains
unresolved; it is not a validated physical correction factor. Numeric padding
and supplied offsets are unchanged pending independent geolocation checks.

**The map overlay applies it too.** This is not automatic: the overlay
resamples a frame into a plate-carrée raster, and gathering each output pixel
from its nominal source pixel would draw the cloud where the line of sight
hits the ground rather than where the cloud is. The same briefing would then
show a cell ~60 km from the position its own annuli reported. `render_overlay`
therefore *scatters* detections to their corrected positions for a
parallax-carrying frame (a gather for a ground-projected one, which needs no
correction). Each complete projected-cell bounding rectangle is painted
lowest-first, so the highest geometric top wins every overlap and supplies
its own temperature. Rectangles approximate projected footprints; they are
not an area-conservative cloud mask.
`nodata` and `undetect` keep their nominal positions — neither carries a cloud
to displace.

`tests/observed/test_sampler.py::test_parallax_is_load_bearing_for_cloud_tops`
pins this: the fixture's FL350 cirrus vanishes entirely when the correction is
removed. A companion test checks that low cloud survives both ways, so the
first test cannot pass for the trivial reason that the correction moves
everything out of range.

### Colour carries temperature, not height

Cloud tops are coloured by **cloud-top temperature**, following the
enhanced-IR ramp pilots already read on satellite imagery (warm → cold: blue,
cyan, green, yellow, orange, red).

Temperature adds thermal information independently of the altitude axis. Both
CTTH temperature and height are retrieved quantities derived from satellite
radiances with atmospheric information; this is not raw L1 brightness temperature.

One deliberate departure from the convention: its warm end is grayscale, and
gray is indistinguishable from the NWP cloud bands this layer exists to be
compared against. Warm tops use a **desaturated blue** instead. The `light`
theme darkens those warm stops further, since a pale blue vanishes on a white
sky; the cold half is identical on every theme, because those hues carry the
meaning and a pilot switching themes must not have to relearn them.

Stops are picked by nearest value, never interpolated — a blended intermediate
colour would imply a precision the 2 km retrieval does not have.
`tests/unit/observed-theme.test.ts` pins both properties across all four
themes: same hue for the same °C, and nothing gray anywhere.

### 3. Never per-station file access, never a full-grid read

The reader pulls **one** window off disk; everything after that is numpy on
that window, with per-station sub-boxes sliced out of memory. The per-airport
research scripts that reopened the granule per waypoint extrapolate to
~100–160 s for a route; this path measures ~42 ms (radar) and ~90–130 ms
(CTTH) on Apple silicon — roughly 1000×.

For CTTH only the *row* range narrows the read: the granule's chunks are
`[23, 5568]` full-width strips, so trimming columns costs a partial-chunk
decompression and saves nothing. `GridWindow.full_width` records that the
caller knows this.

`test_sampler_reads_no_files` monkeypatches `h5py.File` to fail, so a
reintroduced per-station open is caught structurally rather than by a timing
threshold.

### 4. Never a synthetic common timestamp

Each source carries its own `valid_time` and assembly-time `age_minutes`.
Live labels derive age from `valid_time` and the device clock, updating without
weather polling. Each visible source has a distinct badge/window, including
an explicit UTC date and invalid/future-time handling. Map imagery uses its
own response metadata, not the briefing sample's timestamp.
There is no payload-level *observation* time anywhere — not on
`ObservedConditions`, not on `/api/observed/status`.

The one payload-level timestamp is `ObservedConditions.computed_at`, and it is
deliberately not an observation time: it records when the payload was
assembled. No surface renders it as an age, and a test asserts that, because a
single rendered timestamp over four sources that are minutes apart is exactly
the conflation this rule exists to prevent.

DBZH is a max-reflectivity composite from contributing scans in the ten minutes
ending at its nominal time, plus delivery lag. This is not a temporal maximum
of previous composites. `window_minutes` carries that scan interval separately
from display age; lightning carries its own accumulation interval.

### 5. `quality_method` as a histogram, not a count

CTTH commits to **one cloud top per pixel**. The histogram describes the
distribution across nearby retrieval samples. Several height modes do not
establish stacked layers in one column or recover obscured lower cloud.

So `ObservedTopsAnnulus` carries the full per-method breakdown rather than one
confidence number. The FCI guide defines `qm=9` as opaque + RTM + inversion,
not multilayer-suspect; `qm=0` includes unprocessed, and `qm=10` means no
solution. The separate status/processing-quality fields control validity.

Corrected guide-based method table (the original empirical labels were wrong):
`designs/future/satellite-cloud-top-validation.md#quality_method-codes-fci-l2-ctth`.

## Architecture

```
collect  ──►  frames (disk)  ──►  readers  ──►  sampler  ──►  payload
   ▲                                                             │
   │                                                             ▼
scheduler loop                                     briefing.json (inline)
                                                   /api/observed (imagery)
```

The seam that matters: **`collect` is the only module that touches the
network.** Everything downstream reads the local frame store, which is what
makes "zero network fetches inside a briefing request" structural rather than
a rule someone has to remember. `test_no_network_during_payload_assembly`
monkeypatches `socket.connect` to assert it.

| Module | Responsibility |
|---|---|
| `observed/grid.py` | `GridSpec` (proj4 + affine), `GridWindow`, `compute_window`, haversine. **No GDAL/rasterio** — pyproj + numpy is sufficient and validated. |
| `observed/frames.py` | `SOURCE_SPECS`, `GridFrame`/`FlashFrame`, `FrameStore` (write, list, latest, purge). |
| `observed/opera.py` | ODIM_H5 reader: geolocation from `/where`, three-state decode, attribution from `/how`. |
| `observed/ctth.py` | MTG FCI L2 CTTH reader: geos grid from scan-angle radians, parallax fields, `quality_method`. |
| `observed/lightning.py` | MTG LI L2 flash reader (tolerant of baseline variable renames; raises rather than reporting a quiet zero). |
| `observed/collect.py` | OPERA S3 + eumdac fetchers, retention purge, `due_sources`. |
| `observed/sampler.py` | `sample(frame, window, stations, radii)` — one primitive, two call sites. |
| `observed/payload.py` | Builds `ObservedConditions` for a route. |
| `observed/summary.py` | Deterministic "Observed now" text. No LLM. |
| `observed/imagery.py` | Frame → plate-carrée RGBA PNG for the map overlay. |
| `api/observed.py` | `/status`, `/overlay/{source}.png`, `/flashes`. |

### Frame store

`$DATA_DIR/observed/<source>/<YYYYMMDDTHHMM>.{h5,nc}` plus a `.json` sidecar
carrying valid time, receipt time, grid descriptor and attribution — so
"which frame is newest and who made it" is a directory listing, not an HDF5
open.

Payload is written **before** the sidecar, and `has()` keys off the sidecar:
a crash mid-write leaves a frame invisible and re-fetched, rather than
advertised-but-truncated.

Retention is per source because the products differ by two orders of
magnitude in size:

| Source | Cadence | Retention | Frames | ~size/frame | ~total |
|---|---|---|---:|---:|---:|
| `opera_dbzh` | 5 min | 3 h | 36 | 2.5 MB | 90 MB |
| `opera_rate` | 15 min | 3 h | 12 | 1.5 MB | 18 MB |
| `eumetsat_li` | 10 min | 3 h | 18 | 0.4 MB | 7 MB |
| `eumetsat_ctth` | 10 min | **1 h** | 6 | 54 MB | 324 MB |
| | | | | | **≈440 MB** |

Sizes are measured, not estimated: a DBZH `.h5` sampled 2.0–2.7 MB and the CTTH
`.nc` 53.7 MB (the 71.5 MB download is a zip whose quicklook JPEGs are
discarded). Two of them vary with the weather rather than being constants —
DBZH grows with echo coverage and the LI granule with flash count, so an
actively convective day runs nearer 500 MB than 440. Budget 0.5 GB.

CTTH keeps one hour only: a cloud-top field older than that answers nothing a
pilot is asking at D-0, and at 74% of the total it is the only source whose
retention is worth arguing about.

### Collection

OPERA keys are fully deterministic —
`https://s3.waw3-1.cloudferro.com/openradar-24h/YYYY/MM/DD/OPERA/COMP/`
`OPERA@YYYYMMDDTHHMM@0@{DBZH,RATE}.h5` — note the **CloudFerro** endpoint:
the bucket does not exist on AWS, and a wrong host is invisible because a
404 reads as "not published yet"
— so the collector computes the frame times it should have and fetches what is
missing. No listing, no crawl, and a 404 is simply "not published yet".
EUMETSAT products are discovered through `eumdac` over a time window and
arrive zipped; only the netCDF is kept (quicklook PNGs are a quarter of the
bytes and nothing reads them).

Inbound transfer is free — DigitalOcean bills egress only, and the droplet
already pulls ~2 TB/month of GRIB at no cost, so the ~246 GB/month for
10-minute CTTH is entirely on the free side ([issue comment][bandwidth]).
Inbound peaks at ~34× the mean during the GRIB decode cycle, which is why the
collector ticks on its own minute rather than aligning to `:00`.

[bandwidth]: https://github.com/roznet/flyfun-weather/issues/574#issuecomment-5411510994

### Interval sources in the freshness registry

`SourceConfig` was cycle/horizon-shaped and could not express a 5-minute
stream: `cycles` is hours-of-day. It now carries an optional `interval`
(mutually exclusive with `cycles`), and `_floor_to_cycle`/`_next_cycle_init`
branch on which is set. An interval source's `horizon` is zero by
construction — an observation forecasts nothing — and `catalog.build` emits
`horizon_end: null` for it rather than dressing a measurement as a prediction.

The four observed sources are registered with `env_gate="WB_OBSERVED_ENABLED"`,
so a deployment that has not turned the collector on shows nothing at all
rather than four permanently-red rows on the help page.

Their readiness check (`observed_frames`) reads the **local store**, not a
provider: for observed data the question is "how old is the picture I am being
shown?", and that is the valid time of the frame we hold.

## Payload

`ObservedConditions` sits inline on `briefing.json` beside
`route_observations`, and `run_realtime_refresh` recomputes it so the ↻ button
updates it. That re-sample costs no network I/O — the collector has been
writing frames all along — which is why it can ride on the cheap refresh path.

**Imagery is served, never in JSON.** A 2 km composite clipped to a corridor is
hundreds of kilobytes of PNG; putting it in the payload would make every pack
load pay for a layer most of them never draw.

Stations are the route's own interpolated cross-section points, so an observed
value and the model column above it describe the same place. All three radii
(5 / 10 / 20 NM) ship together, so changing the corridor re-resolves the
**sampled annuli** from data already in memory — the cross-section layers and
the route-graph metrics update with no request.

That is a claim about the annuli, not about the whole screen. The map's
corridor box tracks the same setting (it should: the box is meant to show what
you picked), and the box is part of the overlay and flash query strings — so
in `split` layout, where map and graph are both visible, changing the corridor
does re-request the imagery. Imagery is served rather than bundled precisely
because it is too big to ship every radius of, so that request is the design
working, not a leak in it.

Discs are cumulative, not rings: "within 10 NM" is the question a pilot asks.

## Display

| Surface | What it shows |
|---|---|
| Cross-section `observed-tops` (group `conditions`, **default ON**) | Geometric-height histogram ticks + a highest-top cap per route point over NWP cloud bands. Partial detections remain visible beside coverage marks. Per-source age badge. |
| Cross-section `observed-surface` (group `conditions`, default off) | Echo colour strip along the terrain + lightning ticks. Hatched strip for no coverage. |
| Route map | Corridor box, newest frame as a single `imageOverlay`, lightning points age-faded, age badge with attribution. |
| Route graph | `observed-rain-rate` and `observed-flash-rate` metrics, with the corridor selector. Coverage holes render as a distinct baseline state. |
| Briefing section / PDF / digest | The deterministic "Observed now" summary, verbatim in all three. |
| iOS cross-section (group `conditions`) | The same two layers, same defaults, same three-state marks. Corridor picker + per-source ages in the Layers sheet; measured values in the scrub readout, prefixed `Obs` so they never read as forecast. |

### The iOS surface needs no endpoint

The payload is inline on `briefing.json`, and `/packs/{ts}/bundle` ships that
file under its `snapshot` key — so the observed samples already reached the
phone, in the offline download too, before anything on iOS could read them.
Adding an `/observed` endpoint would have been a second way to fetch bytes the
client had.

What iOS *did* need was for a **gated realtime refresh to reach the screen**.
`decide_refresh` already routes every D-0 press to `realtime`, and
`run_realtime_refresh` already re-samples the frames and patches
`briefing.json` — but that path reuses the pack's timestamp, and
`CachingBriefingRepository` is read-from-cache-first. On a downloaded pack the
client therefore re-read the snapshot it already had and showed the copy the
press was meant to replace. (The same staleness had been true of METAR/TAF
since offline packs shipped; observed only made it visible.)

So `RefreshAccepted` and the SSE gate's `complete` event now carry `observed`
alongside `observations`/`sigmets` — the server had already computed it and was
discarding it — and the client folds all three into the loaded snapshot *after*
the pack reload. The follow-up also persists these fields atomically in an
already-downloaded snapshot through the repository, preserving unknown fields,
so reopen/offline cannot revert the successful refresh. The web is unaffected: it
has no client cache and reloads the snapshot from the server anyway.

**Refresh stays a button, on both platforms.** No poll loop. The four sources
update every 5–15 minutes and a re-sample is ~6–12 KB gzipped, so polling would
be affordable — but a briefing that changes under the pilot without being asked
is a different product decision, and the age badges make "how old is this?"
answerable without one.

The ports that have to stay in step are the resolver (`ObservedResolver` ⇄
`buildObserved`), the two layers, and — most easily got wrong, because all three
must agree on one denominator — the band share, the drawing floor and the ramp's
breakpoints. That is the next section.

### What a cloud-top band is a share of, and when it is drawn

A drawn band carries `fraction` — its count over `valid_px`, the retrieval
samples considered valid, cloudy *and* clear. It is a **sample share**, not an
area-weighted sky-cover measurement: parallax-shifted cloudy and nominal clear
samples can overlap and are not deduplicated. The bands sum to the cloudy
sample share rather than always to 100%.
Dividing by `detected_px` instead answers "of the cloud that was found, how
much topped out here?", which inflates as the sky clears: a measured 10 NM
disc holding two cloudy pixels out of 131 drew both bands mid-ramp, as loud
as a solid deck, because each was 50% of the cloud.

The drawing floor uses the same valid-sample denominator: bands at or under
**5%** are not drawn. The fine 1000-ft geometric bands split a distribution
into many bins. This is visual de-emphasis, not evidence of clear gaps or
permission to fly through them. Measured over local packs the cut removes 60% of
the bands at 20 NM while the survivors still account for 87% of the disc's
cloud cover, and 2 route points in 96 lost every band they had — it takes
the noise, not the picture. Those points keep their highest-top cap, which is
drawn from `topsHighestFt` and never passes the filter.

The consequence has to stay visible in the copy: a point with no band is not a
point with no cloud, and the drawn shares sum to most of the cloudy sample share, not
all of it. The help card and the legend both say so.

Where share-based opacity is used, its scale refers to retrieval samples,
not sky area. IR effective cloudiness is cloud amount × 10.5 µm emissivity,
not visual opacity, METAR cloud amount or climb-through advice.

The existing `current-conditions` layer (METAR columns + SIGMET zones) is
untouched — these are siblings, not a replacement. Adding a second layer to
the `conditions` group did require fixing `panel.ts`, which used to hide the
whole group when one named layer was unavailable; it now hides a group only
when *every* layer in it is unavailable, so a working radar layer no longer
disappears with the METAR.

Echo is drawn along the terrain rather than with vertical extent: the
composite is a 2-D surface product, and drawing it with height would invent
structure it does not contain. Cloud tops are where vertical information
legitimately comes from.

### Attribution

Read from each frame's own `how`/`license` metadata, per frame — it is
machine-readable, and the producer varies: one sampled composite was built by
Météo-France rather than centrally by EUMETNET. Surfaced on the map overlay,
in the help-page data-sources table (via the registry `description`), and in
the PDF footer.

### Summary text

`observed/summary.py`, deterministic and no LLM: the digest quotes it as fact,
and a sentence that varied run-to-run would make a briefing look like it
changed when only the phrasing did. Written in aviation shorthand so it needs
no per-locale translation, matching `RefreshDelta`.

Three things the wording is careful about: negative claims are scoped to the
available samples; positive detections survive insufficient coverage; every
clause carries immutable UTC observation times/windows, not a frozen relative
age. It grades
nothing — there is no "severe" or "significant" anywhere in it, and a test
asserts that.

The LLM prompt gets the same string plus per-source ages, and is *not* asked
to reconcile it with the forecast: a model invited to do that would invent
exactly the comparison phase 2 exists to compute properly.

## Configuration

| Variable | Meaning |
|---|---|
| `WB_OBSERVED_ENABLED` | Master gate. Off by default — the collector needs EUMETSAT credentials and ~440 MB of disk, so a deployment opts in. Gates the scheduler loop, the API router, the registry rows and the pipeline stage. |
| `WB_OBSERVED_SOURCES` | Comma-separated subset. Radar without EUMETSAT credentials is half the feature working, not a broken one. |
| `EUMETSAT_CONSUMER_KEY` / `_SECRET` | Data Store OAuth credentials. |

## Testing

`tests/observed/` holds small **synthetic granules with real structure** —
same ODIM group layout, same MTG projection variable, same fill/scale
conventions, a few hundred pixels each (~110 KiB total). The real products are
4–95 MB and none may be redistributed from this repository.
`tests/observed/make_fixtures.py` regenerates them and documents what each
scene is built to exercise.

Two scenes are constructed rather than random:

- the radar composite has a hard-edged no-coverage block over its western
  half, so a station on the boundary exercises the `nodata`/`undetect` split;
- the CTTH granule places its only cirrus **north** of the target station with
  `delta_latitude` set so the corrected position lands on it — which is what
  makes the parallax regression test fail if the correction is dropped.

`tests/observed/test_collect_live.py` reaches the real providers and is
skipped unless `WB_OBSERVED_LIVE_TESTS=1`. It catches the class of bug a
fixture cannot: a renamed variable, a re-cut OPERA domain, a changed LI
baseline, a nodata fraction that has drifted far from the 49.4% the whole
three-state design was calibrated against.

`web/tests/observed-browser.spec.ts`, run with
`web/playwright.observed.config.ts`, exercises the actual briefing entrypoint and
Leaflet using intercepted HTTP and a controlled clock. The entrypoint bundle stays
in memory; no dev server or `web/dist` build is required. Cases cover response
provenance, source/None selection, real refresh clicks, bounded failure retries,
late response/URL cleanup, flash expiry and responsive labels. The legend and
source badge share a renderer-owned normal-flow stack above basemap attribution;
observed controls wrap on narrow layouts. Synthetic images verify decoding and
layout, not provider/scientific correctness. Detailed results and remaining gates:
[verification record](reviews/2026-09-05-observed-fix-verification.md).

## Out of scope

**Phase 2:** model sampling of any kind, `echo_match`/`intensity_match`
verdicts, per-model comparison, advisory wiring, the ETA-vs-observation-time
alignment fork.

The iOS `/observed` endpoint listed here originally is **not needed and not
built**: the payload already rides the snapshot and the bundle (see [The iOS
surface needs no endpoint](#the-ios-surface-needs-no-endpoint)). Still absent on
iOS: the map overlay (which *would* need `/api/observed/overlay`), the route-graph
metrics, and the "Observed now" summary panel.

**Out of the current product scope:** nowcasting and a time slider. The original
decision called these permanently out. The user has since requested exploration;
[the review](reviews/2026-09-05-pr584-observed-review.md) records options and
validation requirements. A cached regional history loop need not require a
global tile service. No prediction or animation is added by the corrective PR.

## Known limitations

- **Cloud-top heights are geometric, not pressure altitude.** Legacy `*_fl`
  fields remain geometric hundreds of feet for compatibility; labels use ft MSL.
  The separate pressure-based `cloud_top_aviation_height` readout already exists
  and retains pressure-FL labelling. Do not replace the geometric axis silently.
- **Overlapping discs double-count lightning.** A flash near two adjacent
  route points is counted in both, which is why the summary says "flash
  detections" rather than implying a flash census.
- **CTTH cannot see thin cloud.** The 2026-05-04 reference flight had a mid
  layer at −5 to −10 °C that L2 CTTH declared "no cloud" because its optical
  depth was below threshold. L1 IR brightness temperature is the right product
  for that, and is gated behind a separate EUMETSAT licence — see
  `designs/future/satellite-cloud-top-validation.md`.
- **Coverage-hatch rendering is implemented three times** with different
  geometry (`observed-tops.ts`, `observed-surface.ts`,
  `route-graph/renderer.ts::drawNoCoverageMarks`), and the codebase has shared
  hatch helpers elsewhere. Consolidating them would stop the three renderings
  drifting apart visually; deferred rather than done here because the three
  geometries genuinely differ (a band, a terrain-hugging strip, a baseline
  tick) and the refactor is wider than this change.
- **`opera.py` and `ctth.py` duplicate** grid-loading, time-parsing and
  attribution structure between `read_metadata` and `read_window`. A shared
  helper is possible; the two products' metadata layouts have little actually
  in common, so it was left alone.
- **The OPERA delivery lag (4 min) is an estimate**, not a measurement across
  many days. Too short and every tick spends a 404; too long and the briefing
  shows an older frame than the provider has. `test_collect_live.py` has a
  loose guard on it.
