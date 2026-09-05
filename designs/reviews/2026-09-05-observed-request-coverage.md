# Observed weather: full-request coverage and remaining work

Audit date: 2026-09-05. Original review: [PR #584](https://github.com/roznet/flyfun-weather/pull/584).
Published corrections: [draft PR #600](https://github.com/roznet/flyfun-weather/pull/600).

This records the requirements audit and subsequent design discussion, **not evidence
that prediction has been implemented**. It incorporates independent read-only
backend/client investigations and a second audit against the user's complete request.

> Superseding design update: the user approved the proposed shared backend and
> **both web and native iOS** vector explorer with "Looks good, pls continue".
> The [written specification](../../docs/superpowers/specs/2026-09-05-observed-motion-design.md)
> and its [record definitions](../../docs/superpowers/specs/2026-09-05-observed-motion-contract.md)
> now capture that direction and await written-spec review. Earlier statements
> below that client coverage or the design direction is undecided are historical.
> Numerical policy choices are unvalidated engineering proposals for that review.
> No prediction code, runtime verification, PR update or deployment is implied.

## Current checkpoint

Read-only GitHub verification confirmed PR #600 is open and draft, targeting
`roznet/flyfun-weather:main`, with head `codex/observed-corrections` at
`95e233b60104e79e3dd05b06ff3ecc54cb9812c6`. Its head is on the existing
`downle/flyfun-weather` fork; no new PR or fork is needed. No GitHub comments or
reviews were present at this check.

The published implementation corrects observations. **It has no history playback,
motion tracking, extrapolation, radar/satellite association, or encounter engine.**
The later request to add prediction reopens that scope; the older observation-only
boundary is not permission to omit the new request.

## Initial request-by-request audit (before design approval)

| User request | Actual status | Remaining acceptance requirement |
|---|---|---|
| Look at PR #584; check incorrect use or interpretation | Reviewed; correction-code findings addressed in PR #600 | Preserve the remaining real-product validation caveats. Not all findings originated in PR #584 itself. |
| Find better ways to visualize radar and satellite | Partly delivered: corrected footprints, provenance, source selection and responsive layout; richer views remain recommendations | Agree which combination of inspection views, history controls and route-relative presentation to deliver; arrows alone do not cover the visualization request. |
| Predict rain-patch movement and speed | Not implemented | Track numeric radar features over several usable frames, showing ground speed/direction and qualified extrapolation. Reflectivity and rain rate remain distinct products, not interchangeable units. |
| Show thunder-related feature movement | Not implemented; previously discussed without explicit acceptance criteria | Attach positive lightning evidence and source times to appropriately associated tracked features. Distinguish tracked echo/core motion from the movement of an electrified system or its anvil; do not label every rain patch or cold top a thunderstorm. |
| Show high-cloud-top movement, including without rain | Not implemented; insufficiently explicit in the previous concrete radar-first proposal | Independently track cloud-top features from usable satellite history. Do not copy radar velocity onto an anvil or omit a high-top feature because radar is absent. |
| Is a feature moving toward or away from the route, and how fast? | Not implemented | Evaluate feature-footprint distance to identified continuous route legs, report closure separately from ground speed, and handle bends, broad features and existing intersections. A missing vector is not evidence of no movement. |
| Link rain to high tops | Not implemented; association limitations researched | Use time-compatible, geolocated footprints with parallax/coverage uncertainty. Show overlap/proximity and ambiguous/unavailable cases; unrelated maxima in the same corridor disc cannot establish a link. |
| Prediction useful to a flight along the route | Proposed extension, not implemented | If presenting encounters, compare feature and planned aircraft position at the same time. Clearly label the distance-proportional planned timing; no live aircraft forecast exists. |
| Save findings in Markdown | Done for the correction review; this file records the expanded gaps | Keep delivered, proposed, deferred and externally unvalidated work distinguishable. |
| Fix, independently re-review, and update the PR | Done for the original correction scope; PR #600 is published as a draft | Prediction still requires design approval, implementation, new tests, independent review and an update to the same PR. Earlier test results do not verify a new feature. |
| Client coverage and deferred Mac testing | Existing corrections cover shared code, web and iOS cross-sections/cache; new prediction client scope is undecided | Agree web-first versus native iOS delivery explicitly. Deferring Mac execution does not silently approve dropping iOS functionality or mean Swift tests passed. |

The principal scope correction is **independent high-top motion**. Radar optical
flow plus a top-height annotation would not satisfy that part of the request.
Thunder-related evidence also needs its own explicit behavior, rather than a
generic label applied to radar objects.

## What the existing review/fixes established

[The detailed review](2026-09-05-pr584-observed-review.md) records F1–F15 and the
source references. Corrections cover documented FCI retrieval/status meanings,
missing versus clear states, retention of positive detections under partial
coverage, advancing per-source ages, lightning windows, geometric versus pressure
altitudes, honest retrieval-sample/cloudiness wording, atomic iOS refresh caching,
satellite footprint/temperature rendering, request lifecycles and mobile controls.

[Verification and independent-review evidence](2026-09-05-observed-fix-verification.md)
applies to that correction scope. The iOS review was static; Mac/iOS execution
remains deferred by user instruction. Synthetic tests establish software behavior,
not forecast skill or correctness of unverified provider packing/geolocation.

## Visualization choices to carry into design

These are candidates, not all already selected for implementation.

1. **Observed-history inspection.** Replay retained regional radar and satellite
   frames with actual UTC times, acquisition windows and visible gaps. Sources
   have different cadences; synchronized controls must not pretend every source
   was observed at one instant. Visually separate observed history from any
   extrapolated interval. Playback alone is not prediction.
2. **Readable radar–satellite context.** Use one primary raster, optional labelled
   cloud-top contours/outlines, and separately identified lightning symbols.
   Keep source times, coverage and legends distinct. Avoid two opaque rainbow
   rasters that obscure each other. Display outlines are approximate, not exact
   cloud boundaries or the geometry to use for scientific overlap calculations.
3. **Feature inspection.** Selecting a feature should expose its observed track,
   ground speed/direction, relevant route leg and closure, top height/temperature,
   rain context and any associated lightning evidence. Preserve separate rain
   and cloud tracks when they disagree. Show unavailable/ambiguous evidence as
   such, not zero speed or a no-storm verdict.
4. **Projected footprints and route timing.** Clearly distinguish observed
   outlines/trails from experimental future outlines. A route-distance × time
   panel with the planned aircraft trajectory can distinguish a crossing before
   arrival from a possible same-time overlap. It is a new view, not a scalar
   metric that the existing graph registry already supports. Uncertainty should
   not be drawn as a calibrated probability band without validation.
5. **Keep the vertical context honest.** Retain geometric top distributions and
   the highest-top marker in the cross-section; nearby height modes are not
   measured layers in one column. Radar maximum reflectivity provides no echo-top
   height. Neither product establishes a safe overflight altitude.

Other possible refinements remain optional: stale-layer fading and a reconciled
observed/model precipitation comparison. Current observed rate (mm/h) and model
accumulation (mm) cannot simply share one scale. Neither refinement substitutes
for the user's motion/association request.

## Approaches and recommendation for discussion

| Approach | Benefit | Trade-off / request coverage |
|---|---|---|
| **Recommended direction: numeric-field motion and object tracking, with independent radar and cloud tracks plus spatial association** | Reuses local observations; directly addresses motion, speed, route relationship and rain/top context | A new subsystem requiring quality gates and replay validation. Compare a simple mask-aware correlation/advection baseline with an established optical-flow/tracking library such as pysteps before choosing the implementation. Its radar examples do not establish CTTH applicability. |
| History playback and manually inspectable overlays only | Smallest useful visualization increment; reveals observed motion/growth without a forecast claim | Does not satisfy the explicit request to add prediction or computed speed/route closure. Requires agreement if chosen as a separate first increment. |
| Integrate a dedicated convective tracking product, e.g. NWCSAF RDT-CW | Potentially richer storm-object/lifecycle information | Requires checking access/licensing, inputs, regional coverage, latency and operations. Not already available in this app, and does not automatically replace independent high-top tracking. HRW atmospheric motion vectors are not necessarily rain-cell motion. |

No algorithm, useful forecast horizon, meteorological threshold or calibrated
probability has been selected or validated by this audit. The recommended product
scope is an explicitly experimental **planned-route situational-awareness** tool,
not tactical storm avoidance or live aircraft guidance. Client coverage needs the
user's decision before this becomes an implementation design.

## Reusable architecture and load-bearing gaps

The [as-built design](../current-conditions.md) and code establish:

- `src/weatherbrief/observed/frames.py::FrameStore.list_frames` exposes complete
  retained frames and per-frame metadata. DBZH is configured for 5-minute cadence
  / 3-hour retention; RATE 15 minutes / 3 hours; LI 10 minutes / 3 hours; CTTH
  10 minutes / **1 hour**. These are storage settings, not proof of uninterrupted
  usable history or an agreed displayed-history length.
- `GridSpec`, windowed numeric readers and separate `nodata`/`undetect` masks are
  reusable. Motion must use numeric, georeferenced data, not rendered PNG colours
  or per-station extrema. Satellite association requires a defensible common
  ground representation, not overlap of the display's bounding rectangles.
- `src/weatherbrief/api/observed.py::observed_overlay` serves only the newest
  raster. A frame manifest and time-specific reads would be new. A cached regional
  history loop does not inherently require a global tile service.
- The collector alone accesses providers. A new analysis path should reuse local
  frames and fail independently of the normal observation briefing. Bound region,
  history, memory and computation; preserve existing refresh/no-polling behavior
  unless a separate change is agreed.
- A motion-analysis region needs enough surrounding data to observe incoming
  features before they reach the chosen corridor. Clipping the analysis to the
  existing display box can hide precisely the approaching patches of interest;
  padding and coverage-edge behavior belong in the approved design.
- `src/weatherbrief/tasks/analyze.py::compute_interpolated_time` supplies
  departure + duration × distance fraction. This is planned, constant-mean-speed
  timing, not wind-adjusted or live ETA. Missing/invalid timing and a departure
  outside the supported experimental interval must disable encounter estimates.
- Web `briefing-main.ts::updateObservedOverlay` currently selects one source at a time
  and suppresses lightning when radar is selected. Combined association viewing
  needs an explicit display policy, provenance and controls, not an unnoticed
  toggle change.
- iOS `RouteMapKitView.swift` currently accepts route/aircraft/airport-forecast
  inputs, not observed imagery. Its `updateRoute` removes all map overlays.
  Native observed/prediction support needs separate overlay ownership, fetching,
  rendering, controls, age/provenance and offline behavior. Shared DTOs alone do
  not provide a native feature; offline old tracks must never appear current.
- No pysteps/OpenCV/scikit-image dependency is declared. NumPy and pyproj are
  existing dependencies; a library choice requires explicit dependency and
  runtime-cost review rather than assuming an installed package is supported.

## Scientific and verification gates

- **Three different motion questions:** ground velocity; feature-edge closure to
  a specified route leg; and simultaneous overlap with the planned aircraft.
  Do not report one as another. Crossing the route line is not automatically an
  aircraft encounter, and a stationary centroid does not imply a stationary edge.
- **Independent cloud motion:** core and anvil can move differently. Growth,
  changing retrieval height/parallax, feature splits/mergers and poor texture
  can masquerade as displacement. Flag or suppress unreliable results rather
  than copying another source's vector.
- **Association is evidence, not storm classification:** preserve time offsets,
  footprints, coverage and geolocation uncertainty. High cold tops with rain do
  not prove a thunderstorm; positive lightning is evidence of electrification,
  while no reported lightning does not prove absence. An unassociated feature
  may reflect missing data or uncertain matching.
- **No future leakage:** `FrameStore.latest(now=...)` checks an age floor, not an
  as-of ceiling, and does not check receipt time. Replay must pin selected frame
  identities and require valid and receipt times no later than its cutoff. Use
  actual frame intervals and reject incompatible grids, duplicates and gaps.
  Historical downloads without contemporaneous receipt records cannot establish
  historical operational latency; distinguish that limitation from an as-received
  replay.
- **Radar acquisition semantics:** OPERA composites use contributing scans from
  the preceding ten minutes and some inputs are already extrapolated. Files at
  consecutive nominal times are not fully independent snapshots. A remaining
  shorthand comment in `SOURCE_SPECS` calls this a maximum over a time window;
  the more precise interpretation is documented in F12 and should be retained
  when extending this area, not used as a new temporal-maximum assumption.
- **Validation before usefulness claims:** compare persistence and simple
  advection with candidate methods on representative regional convective,
  stratiform, quiet and missing-data cases. Measure displacement/spatial error,
  timing error, missed/false route interactions and uncertainty behavior by
  lead time, source availability and growth/decay. Synthetic translations and
  browser fixtures are necessary software tests, not meteorological validation.
- **Unresolved source checks remain open:** actual-granule FCI status/packing,
  IR-cloudiness scale, large low-cloud parallax magnitude/sign, LI footprint and
  quality, and real delivery latency. Parallax particularly affects independent
  cloud tracking and rain/top association. Do not silently guess numeric fixes.
- **Unsupported is not clear:** stale/missing history, weak features, excessive
  time separation, ambiguous matching or invalid planned timing must produce
  explicit unavailability. The existing observation display-age limit is not a
  validated prediction horizon. No safe-route, penetration or overflight claim.

## Initial decision and workflow record

Agree client coverage and the experimental scope, retaining explicit dispositions
for rain, thunder-related evidence, independent high tops and association. In
particular, **web-first and radar-only have not been approved**. The backend can
serve both clients, but iOS map parity is substantial work; the user's Mac-test
deferral remains in force.

Under the requested Superpowers brainstorming workflow this is architectural:
present and approve design sections, write/self-review the spec, obtain the user's
written-spec review, then create the implementation plan. Only afterward implement,
test, independently re-review and update existing PR #600. Do not mark the PR's
prediction exclusions as resolved until the corresponding code and evidence exist.

This audit used repository/document inspection and read-only GitHub metadata.
No prediction code, new runtime tests, live-provider validation, deployment or
PR update was performed during the audit. Publication notes added to the linked
review/preparation records supersede their earlier pre-publication wording without
rewriting historical evidence.

Independent document review identified one scope ambiguity: history controls were
phrased as mandatory before visualization selection. The acceptance wording now
requires agreement on the combination of views; playback is still a proposal.

## Design discussion after the user's continuation

The user subsequently said "Pls continue". The working interpretation is to keep
**both web and native iOS in the proposed increment**, with Mac/iOS execution still
deferred. This advances the recommendation below for design approval; it is not a
claim that a detailed spec, implementation or platform verification is approved.
The earlier request-coverage table remains the delivered-status record.

### Recommended first increment: a shared feature explorer

Use a single, server-side motion and association subsystem with a small,
versioned `observed_motion` payload beside `observed_conditions` in the pack
snapshot. Both clients render the same supplied geometry and numeric results;
they do not independently estimate movement or interpret meteorological evidence.
Keep derived motion separate from measured samples and outside advisory grading,
route-safety colours, overflight logic and LLM-generated operational advice.

The initial algorithm recommendation is **mask-aware local template correlation
with conservative one-to-one feature matching**, performed independently on radar
and cloud-top history. Require at least three usable observation times to check
two displacements, using actual elapsed times and numeric fields; retain footprints
rather than replacing them with centroids. This is an engineering minimum, not
three independent samples: OPERA contributing-scan windows can overlap.
An optical-flow library remains an alternative to compare in replay, not a
requirement to install before there is evidence it improves this use case.

The components have distinct responsibilities:

1. **Frame selection and analysis geometry:** pin eligible local source frames
   as of an explicit cutoff; retain their valid/receipt times and windows;
   bound the surrounding route region; preserve missing/undetect masks. Satellite
   analysis must use corrected ground geometry, not display rectangles.
2. **Independent feature tracks:** detect and match radar and cloud features
   separately. Record measured matching diagnostics, displacement and observed
   footprint changes. Cloud-only scenes remain supported. New, ambiguous,
   splitting/merging, edge-clipped or textureless objects retain observed evidence
   but do not receive a fabricated velocity.
3. **Evidence links:** associate rain/echo, cloud and lightning using compatible
   geometry and source times. Prefer an association time inside both observed
   track histories; do not extend a track beyond its supported interval merely
   to manufacture a link. Links preserve both feature identities and vectors.
   They describe spatial evidence, not a fused storm identity. Without usable
   rain-rate evidence the radar object is labelled an echo, not a quantitative
   rain estimate; lightning detections are not projected as future flashes.
4. **Route relationships and projection:** calculate ground motion separately
   from distance/closure to identified route legs. Project accepted footprints
   under an explicitly constant-motion assumption for server-advertised absolute
   target times. Include a selected-feature table of per-leg relationships and,
   where supported by planned timing and geometry, simultaneous planned-position
   overlap. No extrapolation of initiation, intensity, growth/decay or lightning
   occurrence is implied. No interpolation from waypoint flags substitutes for
   continuous route geometry.

Matching diagnostics should remain observable quantities: usable support,
correlation-peak ambiguity, forward/reverse consistency, disagreement between
successive displacement estimates and held-out-frame residual. These are not a
calibrated probability of a storm encounter. Exact engineering bounds, projection
cap and acceptance tunings belong in the approved spec and its versioned policy;
no scientifically useful horizon has yet been demonstrated.

### Both-client display proposal

Provide a dedicated vector-based prediction mode on web and iOS:

- Independently selectable radar and cloud feature outlines, distinguishable by
  line style as well as colour, plus short observed trails at their source times.
- An observed/projection control selecting server-advertised **absolute UTC**
  times. Solid observed and dashed projected outlines are visibly different;
  a feature unsupported at the selected time must not silently hold its older
  geometry as if it were a prediction for that time.
- A selected-feature card showing ground direction/speed, the named route leg,
  edge distance and closure, top height/temperature, radar/rain context and
  reported lightning, with per-source time and uncertainty/availability labels.
- Selecting a linked rain/cloud pair highlights both independent tracks. Actual
  lightning markers stay at their observed positions and times.
- A small per-feature projection-time/route-leg table supplies timing context
  without requiring a new route-distance-by-time canvas in the first increment.
  Any aircraft comparison is labelled **planned timing**, not live GPS guidance.

In this proposal, full raster-history playback, a new time-distance chart, native
raw-raster parity and live-aircraft prediction are later enhancements, not silently
claimed as delivered. The existing web observed-raster view remains available
separately. A full-screen raster player is not required to deliver independent
feature motion and association, but this choice still needs design approval.

Keep the initial prediction view vector-only. The current raster endpoint serves
latest imagery, while prediction tracks are frozen to their pack inputs. A raw
underlay would therefore require a new frame-addressed imagery contract to avoid
mixing old tracks with a newer picture. This proposal does not create that second
data stream or imply native radar imagery already exists.

### Refresh, cache and failure contract

Build the bounded motion payload from local frames during the existing full/D-0
refresh paths. Reuse the snapshot/offline bundle rather than adding an independent
latest-prediction endpoint. The payload records its run/method version, route and
planned-time identity, source frames, bounds/coverage, independent features,
links, projection times, expiry and explicit unavailability/truncation reasons.
Agree size, geometry and compute budgets in the spec before implementation.

Extend all existing result paths together: `RealtimeRefreshResult`, gated refresh
response, SSE completion, direct observations refresh, web store/snapshot handling,
iOS `RefreshEvent`/snapshot models, in-memory merges, and the shared cache/Siri
refresh path. A failed or invalidated motion refresh must supply an explicit
**unavailable envelope** that replaces old motion. Omitting a nullable field is
not enough: existing observation mergers intentionally preserve fields on nil.

Web owns a separate prediction layer group; iOS separates route-owned from
weather-owned overlays. Map recolouring must not erase weather. Reject results
for a different flight/pack/run after navigation, and clear obsolete selections.
No client weather polling or re-analysis is introduced by age ticks, projection
selection or map redraws.

Downloaded vectors can be inspected offline as **stored analysis** with absolute
dates/times. Never restart their lead-time clock from the current device time.
Expiry removes current-prediction presentation; any historical inspection is
explicitly marked. Native cards must still explain the stored data when basemap
tiles are unavailable. The existing atomic snapshot patch preserves unknown fields
and reports save failures; extend it without creating partial/deleted offline packs.

### Two newly identified prerequisites

1. **Lightning time precision:** `observed/lightning.py::_flash_times` falls back
   to the granule's valid time when individual times cannot be decoded;
   `read_flashes` also falls back when array lengths disagree. That preserves
   positive lightning positions for the existing observation display, but the
   frame carries no flag distinguishing individual times from a window-only
   fallback. Prediction association must preserve this distinction. Window-only
   flashes remain observed context; they cannot masquerade as precisely concurrent
   flashes or silently improve temporal matching.
2. **Refreshed prediction invalidation:** current snapshot/cache merges skip nil
   observation fields. Reusing that behavior for failed motion would leave an old
   forecast appearing to belong to the successful new refresh. The explicit
   unavailable envelope above is a new contract requirement, not a claim of an
   already-fixed prediction bug in a feature that does not exist yet.

The pre-existing unresolved satellite parallax/geolocation validation is also a
load-bearing readiness gate. Unknown or failed geolocation must be distinguishable
from low tracking confidence. A stable pixel match does not validate absolute
cloud position or turn candidate displacement into verified ground speed.
Cloud-route projections and quantitative cross-source associations must be withheld
when their required positioning evidence is unavailable. That status needs a
recorded validation decision for the applicable product/domain, not merely a
confidence disclaimer. Radar-only results may remain usable, but that does not
mark the requested cloud-motion/association capability complete or fully verified.

### Verification and approval boundary

The proposed implementation is opt-in and explicitly experimental. Test independent
radar/cloud movement (including opposing vectors and cloud without rain), stationary
versus unsupported motion, changing masks, gaps, bad timestamps, future-frame
exclusion, grid mismatch, splits/mergers, lightning time precision and association
ambiguity. Test curved/bent routes and footprint edges, closure versus ground
speed, crossing-before-arrival versus simultaneous planned overlap, expiry and
missing planned timing.

Use shared contract fixtures for Python/TypeScript/Swift; exercise failed-refresh
invalidation, cache reload/offline/Siri, navigation races, overlay ownership,
accessible narrow-screen controls and no polling. Run isolated backend/web/browser
checks. Prediction Swift tests will be written and statically reviewed; Mac/iOS
execution remains deferred and must not be reported as passing.

Regional replay against persistence and simple advection remains necessary before
any forecast-skill or useful-horizon claim. Source geolocation/packing and actual
receipt-time evidence remain separate from synthetic software verification. The
implementation can be reviewed in draft PR #600 without claiming operational
validation or deploying/enabling it.

Next: obtain approval of this proposed design, then write and commit the detailed
spec, self-review it and obtain the requested written-spec review before the
implementation plan/code. No prediction implementation or PR update occurred
during this design discussion.

Independent proposal review caught the Swift-test sentence phrased as completed
work. It now explicitly describes future prediction tests; only the earlier
correction tests exist today. No other actionable proposal findings were reported.

## Written-spec checkpoint after design approval

The subsequent "Looks good, pls continue" approved the design direction, including
independent radar/cloud tracks, lightning evidence, route closure and planned-time
overlap on both clients. Full raster playback, raw-raster iOS parity and a new
time-distance canvas are deferred from this increment. The formal spec supersedes
the proposal's undecided algorithm/limit/contract wording, but does not establish
forecast skill or satellite registration readiness.

Two independent read-only **specification** reviews and main-agent self-review
produced these changes. They are design corrections, not implemented bug fixes:

| Review finding | Resolution in written spec |
|---|---|
| Interior patches can match while changing nodata clips the feature boundary. | Feature-level known support and a one-cell rim must remain valid; define exact translated common support, including fractional shifts. |
| Work/minimum-size caps can hide a second parent/child and falsely imply unique tracking. | Label the full bounded field and include small/unselected components in lineage checks; incomplete checks withhold acceptance. |
| An older accepted chain could be attached to a newly unmatched feature. | Accepted chains end at the newest reference; specify the earlier-pair next-observation residual mathematically. |
| "Up to two" patches and unspecified combination permit different acceptance results. | Require two usable patches, deterministic refinement/fallback and mean displacement; define reverse consistency. |
| Projected x/y direction is not necessarily local true bearing. | Convert a defined reference point's one-second translation through inverse projection/WGS84 geodesics; retain grid translation for geometry. |
| Nominal timestamps alone can admit an acquisition ending after cutoff. | Validate actual acquisition/receipt intervals and explicitly define the canonical valid-time reference. |
| Cached-ready iOS packs can skip capability-bearing network reads. | Unknown initial authority; one bounded cache-bypassing existing-snapshot read before active styling, with stored-only failure behavior and no polling. |
| Nested DTO names/status/null rules were underspecified. | Add normative shared record definitions and fixture requirements for Python/TypeScript/Swift. |
| Marker caps can remove every displayed flash linked to a retained feature. | Preserve per-feature lightning count/window/status/completeness before marker selection; known positive evidence remains on the card even with no retained map marker. |
| Atomic control-file replacement can invalidate a lock, and first creation/deletion were unspecified. | Separate stable lifecycle lock, generation token, authorized full-writer creation and deleted-pack refusal. Follow-up contract review requires retaining the revision high-water mark across same-URL recreation, so older cached results cannot outrank the new generation. |
| Remaining implementation choices were ambiguous. | Fix the supported radar spacing/raw-read caps, missing-publication versus corrupt-frame behavior, conditional planned solver scope and no result-cache increment. |

The next user checkpoint is review of the written specification. The requested
Superpowers brainstorming workflow pauses here before `writing-plans` and code.
Mac/iOS execution remains deferred. The published PR still contains observation
corrections only; eventual prediction delivery needs implementation, new tests,
independent code review and an update of existing draft PR #600.
