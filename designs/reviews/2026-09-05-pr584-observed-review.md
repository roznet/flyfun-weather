# PR #584: observed radar, lightning and satellite review

Review date: 2026-09-05. Original PR: <https://github.com/roznet/flyfun-weather/pull/584>.

## Scope and conclusion

The existing architecture is a useful observation display: numeric local frames,
three-state radar coverage, parallax before satellite corridor membership,
per-source timestamps, cumulative corridor discs, and separate surface/top layers.
It is not a motion estimate, a storm classification, or an aircraft encounter
prediction. PR #584 mainly brings the existing observations to iOS and merges
realtime results after reloading a pack. Most scientific interpretation problems
reviewed here originate in shared pre-existing code, not in that PR alone.

The corrective branch is `codex/observed-corrections`, based on upstream main
`b48d9a8ff5831ab44fc3e43253c808578c215277`. It does not add a nowcasting subsystem.
The fixes and independent re-reviews are prepared locally for user review, before
any push or GitHub submission. See the [local PR draft](2026-09-05-observed-fix-pr-draft.md)
and [verification/reviewer record](2026-09-05-observed-fix-verification.md) for
implemented corrections, additional review-round findings and remaining gates.
Recommendations below are not all claims of implemented changes.

Original PR head: `67d79414ab15661d9a22d42132689f3342dc0cca`; original base:
`b6271e4a89a1118feaa56ae111167671c6c28f75`; merged 2026-08-27 as
`64efff6c39d7740d52c0ff3e599f239c37699888`.

## Confirmed correctness findings

File references describe the reviewed behavior; line numbers can move in the fix.

### F1 — FCI retrieval methods were assigned the wrong meanings (high)

`observed/ctth.py`, summary, both client resolvers and cap colouring interpret
method 9 as multilayer-suspect and method 10 as semitransparent. The exact **MTG
FCI CLM, CT and CTTH** guide says otherwise. This must not be inferred from the
similarly named NWCSAF CTTH product or from correlations in a small sample.

| `quality_method` | Meaning in the FCI guide, Table 10 |
|---|---|
| 0 | Not processed: missing/corrupt data **or** cloud-free |
| 1 | Opaque and RTM |
| 2 | Opaque minus RTM |
| 3 | Intercept IR10.5/IR13.4 |
| 4 | Intercept IR10.5/IR6.3 |
| 5 | Intercept IR10.5/IR7.3 |
| 6 | Radiance ratio IR10.5/IR13.4 |
| 7 | Radiance ratio IR10.5/IR6.3 |
| 8 | Radiance ratio IR10.5/IR7.3 |
| 9 | Opaque + RTM + inversion; **not a multilayer flag** |
| 10 | No solution; **not a semitransparent retrieval** |

Remove unsupported multilayer percentages, labels and warning colours. A method
histogram describes retrieval methods across samples, not confidence of the
particular pixel that supplied the highest top.

### F2 — Method 0 cannot establish clear sky (high)

The old decoder uses `quality_method == 0` for `undetect`. But method 0 includes
unprocessed/corrupt pixels, so it can turn missing observations into positive
clear-sky evidence. Finite heights with absent/failed quality can also leak through.

Use the product's status and processing-quality fields. The guide distinguishes
corrupt/unprocessed (0), cloud-free (1), cloud retrieval failed (2), cloud retrieval
successful (3), plus separate dust/ash statuses (4–7). Processing quality is
unprocessed (0), poor (1), good (2). Unknown, masked and contradictory states need
explicit handling. Synthetic fixtures must independently exercise these cases;
passing fixtures built around the wrong enum is not scientific validation.

### F3 — Displayed ages stop advancing (high)

The iOS `ObservedTopsLayer` helper and web `observed-tops.ts` prefer saved
`age_minutes`. Reproduction: a frame truly 54 minutes old still displayed
`Radar 06:00Z · 5 min old`. Merely calculating a new age inside the formatter is
insufficient if no redraw occurs while the page remains open.

Derive age from `valid_time` and the current clock; invalidate cheap labels
periodically and on foreground/visibility changes. Do not poll weather or rerun
expensive route extraction for an age update. Missing/invalid/future timestamps
must not become "0 min old". Include UTC date for old/offline packs and qualify
stale sources independently.

### F4 — iOS realtime results do not survive cache reload (high)

`BriefingViewModel` merges returned observations, SIGMETs and observed conditions
only into `snapshotState`. `CachingBriefingRepository` remains cache-first;
unchanged pack timestamps bypass ordinary sync/download replacement. Refresh,
close, then reopen/offline can therefore revert to the old snapshot.

Persist the same refreshed fields through the existing repository/cache boundary
before the in-memory merge. Preserve unrelated and unknown JSON fields and avoid
creating a partial offline pack. Cover refresh → reload with actual cache data.

### F5 — Missing coverage must not become whole-corridor clear (high)

`observed/summary.py` skips missing/insufficient top samples then can conclude
"clear over the whole corridor". Direct reproduction: one clear station plus one
unavailable station produced precisely that claim. Distinguish clear at sampled,
sufficiently covered locations from unobserved locations. Even an adequately
covered disc is not a complete census of every cloud or all intervening space.

### F6 — Partial coverage hides positive detections (high)

Web `data-extract.ts` and the analogous iOS resolver discard extrema/bands when
coverage is insufficient. A real intense return or high top then disappears.
Preserve measured positive evidence and show a partial-coverage warning/hatch
alongside it. Poor coverage weakens absence claims; it does not erase a detection.
This rule must reach renderers and tooltips, not stop at the resolver.

### F7 — Lightning windows and source badges misrepresent time (high)

The summary says flashes occurred "in the last 10 min" even for a delayed frame;
the zero-flash branch omitted age. A 10-minute frame ending 25 minutes ago covers
35–25 minutes ago, not now. Show the actual interval and reference time for both
positive and zero detections. Overlapping station discs double-count flashes;
call sums detections, not distinct flashes.

The surface layer chooses radar **or** lightning for one badge while showing both.
Render separate source timestamps/windows. Rain rate, reflectivity, lightning and
tops do not share one observation time. A fetched **latest map image** also must
not inherit an old briefing-snapshot timestamp; its metadata must describe the
actual returned frame, or the UI must explicitly identify that provenance limit.

### F8 — Geometric heights are labelled as flight levels (high)

The legacy `metres_to_fl` and `*_fl` height/histogram fields are geometric hundreds
of feet MSL, not pressure flight levels. Bare `FL` labels imply an altimeter datum
the numbers do not have. Label converted heights as feet MSL/geometric. The
separate `cloud_top_aviation_height` pressure-based chip already exists; it is not
a missing feature. Keep its pressure-FL label distinct and retain existing wire
keys for compatibility.

### F9 — Effective cloudiness is not visual opacity or METAR cloud amount (high)

FCI effective cloudiness is pixel cloud amount × emissivity at 10.5 µm. Labels
such as "solid", "broken", "thin" and suggested climb-through judgments claim
more than this quantity measures. Use neutral **IR effective cloudiness** language.
It is not a BKN/OVC report or an assessment of whether cloud can be penetrated.

The guide documents percent, while repository research reports decoded 0–1
values despite metadata. Packing/scale conventions require real-granule validation;
do not blindly multiply/divide by 100 or guess from field names.

### F10 — Temperature overlay selects the warmest overlapping cloud (high)

`imagery._scatter_parallax_detections` applies `np.maximum.at` to the plotted
quantity. For temperature that selects the warmest pixel, contrary to the
highest-top precedence advertised by the renderer. Direct reproduction with
overlapping heights `[12000, 1000]` m and temperatures `[220, 280]` K produced the
warm RGBA `[127, 157, 196, 190]`.

Resolve overlap by geometric top height and carry the winning pixel's plotted
quantity. Regressions should cover reversed input ordering, overlapping footprint
edges as well as identical centres, inversions (highest need not be coldest), and
missing auxiliary values.

### F11 — Satellite footprint approximation is undersized and displaced (medium)

`_source_pixel_block` converts geostationary projected metre steps using
`/1000/111` for both axes, ignoring projection distortion and longitude latitude
dependence. At the LFAT fixture the true local footprint is approximately
0.03629° latitude × 0.03005° longitude, against the assumed 0.01802° each. Blocks
also extend only southeast from the pixel centre, and clipping can pile
out-of-bounds writes onto an image edge.

A bounded rendering correction can derive footprints from the grid projection
and centre them correctly. A fully area-conservative, parallax-warped polygon
resampler is a separate improvement; approximate raster footprints must not be
presented as an exact cloud boundary.

### F12 — Reflectivity temporal semantics need more precise wording (medium)

The CIRRUS max-reflectivity sheet describes a five-minute composite from radar
scan pixels acquired in `[NT−10 minutes, NT]`. "Rolling 10-minute maximum" can
mislead readers into thinking it is a temporal maximum of previous composite
images. Preserve the ten-minute contributing-scan window, but describe its
semantics accurately. Some Spanish ten-minute inputs are already extrapolated
with Lucas–Kanade to support the five-minute composite cadence, an important
caveat before estimating motion from these images.

## Additional browser follow-up findings

These were reproduced while verifying the local correction package after commit
`359373818d40f6b2031e70f73ae074f08aa642da`, not newly attributed to PR #584 alone.

### F13 — The source selector can contradict the displayed map (medium)

With a saved satellite preference and unavailable cloud-top data, the renderer
falls back to radar but `controls/panel.ts` selects against the missing saved
option. The browser then shows None while radar is visible. The opacity control
can disagree for the same reason. Both controls now use the renderer's shared
resolved source, without overwriting the preference. Explicit None still disables
the overlay. Reproduced and fixed through the real page, not only a pure selector.

### F14 — Refresh clicks multiply after observation-panel renders (medium)

`managers/briefing-ui.ts::renderRouteObservations` added another delegated click
listener on every render. One refresh click sent two POSTs in both raster and
lightning recovery journeys; more renders could accumulate further handlers and
stale popup closures. Replacing the panel-owned `onclick` handler keeps one
current delegate. A repeated-click browser regression verifies one POST per click.

### F15 — Phone layouts hide provenance and controls (medium)

The legend's fixed `bottom: 28px` position assumed a single-line source badge.
Long dated attribution wrapped behind the legend and into the basemap attribution;
the non-wrapping observed-controls row pushed its opacity readout off-screen.
Simple x-bound checks missed the overlap, which was found by inspecting screenshots.

A renderer-owned, bottom-left normal-flow stack now keeps legend and source badge
separate, above basemap attribution. Controls wrap at narrow widths. Browser
regressions check non-overlap, slider/readout bounds and teardown at 1280, 390 and
320px with long dated labels. Do not move the legend into the top-right corner:
the existing airport-forecast legend already occupies that position.

The browser harness itself also needed an integration correction: the repository's
default runner uses a different base URL. Absolute navigation to the intercepted
fixture origin avoids a fixture-generated 404 when discovered by that runner.
The no-server alternate-base reproduction failed before this change and passed
afterward. All ten focused tests then passed independently under that base URL.

## Other limitations and unresolved evidence

These are deliberately distinguished from verified bugs and are not silently
"fixed" by guessing new data semantics.

- **Parallax magnitude/sign validation:** correction before corridor membership
  is structurally right, but the fixture was constructed with large offsets. It
  does not validate claims of 40–50 km displacement for low clouds or a
  factor-of-four discrepancy from viewing geometry. Recheck original packed
  values, decoded units, scan-angle grid, signs and independent geolocation on
  actual granules. Do not change numeric correction factors without that evidence.
- **Sample share is not exact sky-area fraction:** shifted cloudy samples and
  nominal clear samples are neither deduplicated nor area-weighted. A synthetic
  5 NM example changes from 32 nominal samples (8 cloud, 24 clear) to 53 corrected
  samples (29 cloud, 24 clear). Describe histogram/cloud shares as fractions of
  valid retrieval samples, not measured fraction of sky area. A true area fraction
  needs common-ground-grid resampling with an explicit overlap/coverage rule.
- **Histograms are not vertical cloud stacks:** multiple modes are distributions
  among nearby pixels, not two measured layers in the same column. One retrieved
  top per pixel cannot reveal all underlying layers. Missing/suppressed bins are
  not evidence of a clear gap. Preserve a highest-top marker even when small bins
  fall below a visual threshold, but do not turn an extreme into a dominant deck.
- **Lightning coverage:** MTG LI covers about 84% of the Earth disc, not the entire
  disc. The present point payload has no explicit footprint/quality mask. Zero
  means no flashes reported in that window, not guaranteed absence of convection.
  Detection efficiency, footprint, outages and product quality need independent
  validation before stronger negatives or radar/LI coverage fusion.
- **Satellite retrieval limits:** cloud-top height is retrieved using radiances
  and atmospheric information, not a direct height measurement. Semitransparent
  and multilayer situations can be difficult; CTTH is not a complete cloud census.
  L1 IR, OCA or other products merit separate investigation, not assumed equivalence.
- **Radar limits:** a 2-D maximum-reflectivity composite is not a vertical echo-top
  profile. Weak returns can include drizzle/non-precipitation/clutter; display
  thresholds are visual conventions, not safe-routing thresholds. Attenuation,
  bright band and coverage quality matter for storm interpretation.
- **Latency estimates:** provider delivery lag estimates have not been verified
  statistically across days. Product timestamp/window, receipt time and current
  display age are different concepts.
- **Existing improvements:** the PR discussion's absent-source/zero-flash and
  pressure-altitude-chip issues had already been corrected by its final head;
  do not re-report all prior review comments as still-open regressions.
- **Existing design boundaries:** the as-built design excludes animation and
  nowcasting. Reopening that is an explicit future product decision. A history
  loop does not inherently require global tile infrastructure.

## Better visualization: recommended progression

Keep each view responsible for one question and synchronise selections between
them. Avoid opaque radar and satellite rainbows painted over each other.

1. **Observation inspection first.** Map: reflectivity colours, optional labelled
   cloud-top contours, lightning symbols, clear source times/coverage. Cross-section:
   geometric top distribution, dominant versus extreme top, pressure datum where
   available, retrieval limitations. Add a short observed-history loop from cached
   regional frames to reveal movement and growth without implying prediction.
2. **Motion and short extrapolation next (recommended experiment).** Estimate
   optical flow on numeric, georeferenced radar grids and track echo objects across
   frames. Overlay displacement arrows and a widening, explicitly experimental
   future footprint. Preserve track confidence, splits/mergers, age and growth/
   decay diagnostics. A route-distance × time panel with the planned aircraft
   trajectory makes a potential encounter easier to understand than one arrow.
3. **Evaluate a dedicated convective product.** NWCSAF RDT-CW provides a relevant
   conceptual/data-processing path for convective objects and their lifecycle.
   Check regional coverage, access/licensing, required inputs, latency and operating
   cost. HRW provides atmospheric motion vectors from cloud/water-vapour tracking;
   those are not automatically precipitation-cell motion.

No bitmap mockup or new UI subsystem is required to make this corrective PR useful.

### "Moving toward or away from my route, and how fast?"

Distinguish three quantities:

| Quantity | Meaning |
|---|---|
| Ground motion | Feature velocity over Earth, e.g. direction and knots |
| Route closure | Rate of change of distance from the moving feature's edge to a particular route segment |
| Encounter | Whether the feature footprint and aircraft position overlap at the **same future time** |

In a local metric projection a first experiment propagates a footprint as
`F(t) = F(t0) + v × (t − t0)`. Evaluate its distance/intersection with each route
leg and the aircraft's planned position at that time. A stationary route and a
moving aircraft are different comparisons. Route bends, broad anvils and feature
growth make one signed centroid distance unreliable. Use footprint edges, actual
frame timing and consistent units; arrival at the route line is not necessarily
an aircraft encounter.

Track displacement across several independent observations; suppress unreliable
vectors for stale frames, weak texture, edge-of-coverage, failed associations,
splits/mergers and rapidly changing objects. Show uncertainty and provenance, not
an unvalidated precise arrival time or probability. Do not promise a fixed useful
lead horizon until regional verification supports it. Extrapolation alone cannot
predict convective initiation or reliably handle rapid growth/decay.

### "Is the rain linked to those high tops?"

Not from current corridor extrema alone. The maximum radar return and maximum
satellite top within a 20 NM disc may belong to different objects. Independent
summary numbers discard exactly the position information needed for association.

A future association should retain radar footprints, time-compatible and
parallax-corrected CTTH pixels, lightning detections and object lifecycle. Measure
spatial overlap/proximity with time and geolocation uncertainty; separate the
precipitating core from a spreading non-precipitating anvil. High cold tops plus
rain support a convective interpretation in context, but do not prove a
thunderstorm. Lightning is stronger positive evidence of electrification; absence
of reported lightning does not disprove convection. Echo top and satellite cloud
top are different quantities and neither provides an overflight clearance.

### Validation before a prediction feature ships

- Replay archived frames with strict observation cutoff; do not leak later frames
  into motion/association estimates. Account for already-extrapolated radar inputs.
- Compare persistence, simple advection and candidate tracking/nowcast methods on
  UK/European convective and stratiform cases, including quiet and missing-data cases.
- Score route/aircraft encounter misses, false alarms, timing error, spatial error
  and uncertainty calibration, not just visually attractive loops.
- Stratify by lead time, growth/decay, radar coverage and satellite/LI availability.
- Resolve the product priority (preflight planning versus in-flight strategic
  awareness), acceptable latency and validation criteria with the user.
- These delayed remote products are for situational awareness, not tactical
  thunderstorm penetration, separation or assurance of a safe route/altitude.

## Sources and evidence limits

1. EUMETSAT, **MTG FCI CLM, CT and CTTH Data Guide**, Tables 9–10:
   <https://user.eumetsat.int/resources/user-guides/mtg-fci-clm-ct-and-ctth-data-guide>.
   Public guide-content endpoint used to inspect the exact tables:
   <https://user.eumetsat.int/strapi/api/user-guides/239>.
2. EUMETNET/OPERA, **Max Reflectivity Product Sheet**, edition 2.0 (2024):
   <https://www.eumetnet.eu/wp-content/uploads/2024/06/OPERA_Max-Reflectivity_Product-Sheet_Ed-2.0.pdf>.
3. NWCSAF RDT-CW: <https://www.nwcsaf.org/rdt_description_2025>;
   HRW: <https://www.nwcsaf.org/hrw_description_2025>;
   delivery conditions: <https://www.nwcsaf.org/software-delivery-conditions>.
4. pysteps: <https://pysteps.readthedocs.io/en/stable/> and
   <https://pysteps.readthedocs.io/en/stable/auto_examples/thunderstorm_detection_and_tracking.html>.
   Example tuning for Swiss radar is not automatically valid for OPERA composites.
5. Pulkkinen et al. (2019), pysteps and precipitation nowcasting limitations:
   <https://gmd.copernicus.org/articles/12/4185/2019/>.
6. MTG instrument coverage: <https://www.eumetsat.int/meteosat-third-generation-instruments>.

The original review ran 116 targeted Python tests and 64 web tests. The current
upstream baseline ran 142 Python tests (6 deselected) and 68 web tests. These are
baselines, not final verification of this corrective branch. Tests are synthetic;
no live-provider credentials or operational weather were used to validate a
forecast. The Linux workspace has no Xcode/Swift runtime, and the user has deferred
Mac/iOS execution. It remains unverified, not silently waived as successful.
Final checks and independent-review dispositions are documented separately.
