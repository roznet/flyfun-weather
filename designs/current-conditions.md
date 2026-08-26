# Current Conditions — Observed Radar, Lightning and Cloud Tops

> Phase 1 of #574. **Displays observations only** — it computes no verdict and
> touches no advisory.

Shows what a pilot can actually *see* along the route right now, next to what
the models forecast: OPERA radar reflectivity and rain rate, EUMETSAT MTG
total lightning, and EUMETSAT MTG satellite cloud tops.

The cross-check still happens — `observed-tops` renders directly over the NWP
cloud bands, so "model says FL120, satellite saw FL280" is visible to the eye.
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
> Neither is a correctness gap — the age is always on screen and coverage is
> never implied — but both are real divergences from a rule that was written
> down, and belong in phase 2's display pass.

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

`insufficient_coverage` (below `MIN_COVERAGE_FRACTION`, 0.35) is the signal
that a sample must not be asserted at all. The route graph renders it as a
distinct hatched state on the baseline, never as a gap — a gap in a rain-rate
line reads as "no rain".

On the satellite side the same discipline applies with different vocabulary:
`quality_method == 0` means *no cloud*, a positive observation, and maps to
`undetect`. Off-disc and failed retrievals are `nodata`.

Lightning is the exception, and deliberately so: it is a point product and the
imager sees the whole disc, so an absence of flashes is a real observation.
`ObservedFlashAnnulus` therefore has no coverage split at all.

This matters *more* in phase 1 than it will in phase 2, because no comparison
exists yet to carry confidence.

### 2. Parallax before corridor membership

The satellite sits over the equator. Its line of sight to a cloud at 50°N
continues past the cloud and strikes the ground *north* of it, so the pixel
containing a cloud-top claims a ground position tens of kilometres away.

**Measured displacement: 52 km median, against a 37 km (20 NM) corridor.** An
uncorrected sample is not slightly wrong — it describes a different place.

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
value — the naive `h × tan(zenith)` formula underestimates real dlat by
roughly a factor of four, which is why the product ships a correction field at
all.

**The map overlay applies it too.** This is not automatic: the overlay
resamples a frame into a plate-carrée raster, and gathering each output pixel
from its nominal source pixel would draw the cloud where the line of sight
hits the ground rather than where the cloud is. The same briefing would then
show a cell ~60 km from the position its own annuli reported. `render_overlay`
therefore *scatters* detections to their corrected positions for a
parallax-carrying frame (a gather for a ground-projected one, which needs no
correction), painting lowest-first so the highest top wins an overlap.
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

Temperature rather than height because temperature is what the instrument
*measures* — height is derived from it against a model profile, so colouring by
height would put a modelled quantity in the place of the observation.

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

Each source carries its own frame's `valid_time` and `age_minutes`, badged on
the layer. There is no payload-level *observation* time anywhere — not on
`ObservedConditions`, not on `/api/observed/status`.

The one payload-level timestamp is `ObservedConditions.computed_at`, and it is
deliberately not an observation time: it records when the payload was
assembled. No surface renders it as an age, and a test asserts that, because a
single rendered timestamp over four sources that are minutes apart is exactly
the conflation this rule exists to prevent.

DBZH is a **rolling 10-minute maximum** plus delivery lag, so an on-screen
echo can be ~15 min old: about 30 NM of own-ship at 120 kt. `window_minutes`
carries that separately from the age, and the map badge says "10 min rolling
max" so a maximum is never read as a snapshot.

### 5. `quality_method` as a histogram, not a count

CTTH commits to **one cloud top per pixel**. For a cirrus-over-stratus stack,
adjacent 2 km pixels flip between "high" and "low" as cirrus opacity wobbles
around the retrieval's threshold; a single-pixel sample gets an arbitrary
slice of that. Only the histogram over an annulus recovers the structure.

So `ObservedTopsAnnulus` carries the full per-method breakdown rather than one
confidence number. `qm=9` is the multi-layer-suspect flag — precisely the case
where committing to one number is least trustworthy — and `qm=0` is a positive
observation of clear sky. Collapsing them loses both.

Empirical method table:
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
| Cross-section `observed-tops` (group `clouds`, **default ON**) | FL-band histogram ticks + a solid highest-top cap per route point, over the NWP cloud bands. Hatched mark where the retrieval could not answer. Age badge. |
| Cross-section `observed-surface` (group `conditions`, default off) | Echo colour strip along the terrain + lightning ticks. Hatched strip for no coverage. |
| Route map | Corridor box, newest frame as a single `imageOverlay`, lightning points age-faded, age badge with attribution. |
| Route graph | `observed-rain-rate` and `observed-flash-rate` metrics, with the corridor selector. Coverage holes render as a distinct baseline state. |
| Briefing section / PDF / digest | The deterministic "Observed now" summary, verbatim in all three. |

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

Three things the wording is careful about: it never asserts a clear sky it did
not see (an insufficient-coverage disc reads "no radar coverage over N of M
points", not "no echo"); every clause carries its own age; and it grades
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

## Out of scope

**Phase 2:** model sampling of any kind, `echo_match`/`intensity_match`
verdicts, per-model comparison, advisory wiring, the ETA-vs-observation-time
alignment fork, the iOS `/observed` endpoint.

**Permanently out:** nowcasting, and a time slider. The map draws one frame,
not a loop — a tiled animated radar product is a different, much more
expensive thing than the question this layer answers.

## Known limitations

- **Cloud-top heights are geometric, not pressure altitude.** The product also
  ships `cloud_top_aviation_height`, which is what an altimeter would agree
  with, but its documented units (`FL/10`) have not been verified against a
  real granule. Adopting it is a phase-2 change once that is checked;
  `metres_to_fl` is the single place it would change.
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
