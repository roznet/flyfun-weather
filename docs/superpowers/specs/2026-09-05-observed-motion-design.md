# Experimental observed-weather motion and route context

Date: 2026-09-05. Follow-up: [PR #600](https://github.com/roznet/flyfun-weather/pull/600),
following the review of [PR #584](https://github.com/roznet/flyfun-weather/pull/584).

**Status: written specification approved; implementation in progress under the
[implementation plan](../plans/2026-09-05-observed-motion.md). No completed prediction implementation is claimed.** The user approved the proposed
shared backend and native/web vector explorer with "Looks good, pls continue".
After written-spec review, the user explicitly approved continuing with implementation.
Mac/iOS execution remains deferred. The published correction head is still
`95e233b60104e79e3dd05b06ff3ecc54cb9812c6` at the time of this specification.

## 1. Objective and completion boundaries

Help inspect whether an observed radar feature or high-cloud-top feature is moving
toward or away from a planned route, at approximately what speed, and whether
radar, high tops and reported lightning have compatible spatial/time evidence.
Show short, explicitly constant-motion footprint projections on **both web and
native iOS**. This is an experimental situational-awareness tool, not an advisory,
tactical avoidance system, safe-route decision or overflight clearance.

The implementation must cover each requested feature family, rather than calling
radar arrows alone complete:

| Requirement | Spec behavior |
|---|---|
| Rain-patch motion | Independently tracked radar-echo contours; matched rain-rate evidence is separately timed and never inferred by converting dBZ. |
| Thunder-related motion | A tracked feature can carry positive, time-compatible lightning evidence. Its label is "feature with reported lightning", not a diagnosed thunderstorm. Flashes themselves are not forecast. |
| High-top motion without rain | Independent satellite high-top contours and tracks; radar availability is not a prerequisite. Ground-motion publication has a separate geolocation-evidence gate. |
| Toward/away and speed | Ground speed/bearing, footprint-to-leg distance and signed closure are separate quantities. |
| Rain linked to high tops | Many-to-many, qualified footprint-overlap/proximity links at a documented comparison time; no fused storm identity or pairing of unrelated corridor maxima. |
| Better visualization | Separate feature families, observed trails, projected outlines, selection/detail cards, absolute-time controls and a route-leg/time table on both clients. |
| Flight timing | Continuous overlap calculation when valid planned timing is available; uses the existing distance-proportional **planned** trajectory, not live GPS or wind-adjusted arrival guidance. The solver is in scope; its result is conditional on usable inputs. |

Three completion claims must remain separate:

1. **Software implemented:** algorithms, contracts, both UIs, persistence and
   regression tests exist; written Swift tests may remain unexecuted under the
   user's Mac deferral.
2. **Source readiness:** applicable source registration/packing evidence is
   recorded. Unverified satellite positioning does not become verified because
   a synthetic tracking test passes.
3. **Predictive usefulness:** regional replay supports a stated use and lead
   interval. No such claim is established today. Research code can remain in a
   draft PR with its deployment gate off while this evidence is missing.

Do not describe the full live cloud-motion/association request as completed while
the required geolocation gate still withholds those results. Record that as a
remaining evidence dependency in the PR and handoff.

### Explicit exclusions

No full raster-history player, new route-distance-by-time canvas, native raw-raster
parity, new provider integration, live-aircraft prediction, convection initiation,
intensity/growth forecast, predicted flashes, calibrated probabilities or advisory
colour changes. Existing observed imagery/cross-sections remain available. The
prediction map has no latest-raster underlay; a future underlay requires immutable,
frame-addressed imagery matching the analysis. These exclusions were presented
with the approved first-increment design, not inferred from a web-only shortcut.

## 2. Architecture and ownership

One backend owns all scientific calculations. Clients only validate, select and
render supplied results. Add an optional, versioned `observed_motion` sibling to
`observed_conditions` on the snapshot. Use that exact key on every refresh response
and SSE completion as well. Bounded vectors travel through the existing snapshot
and offline bundle; no independent latest-prediction endpoint is introduced.

| Unit | Responsibility and dependencies |
|---|---|
| `models/observed_motion.py` | Typed version-1 wire contract; no provider, storage or rendering logic. |
| `observed/motion/policy.py` | Versioned engineering limits and feature definitions below. |
| `observed/motion/history.py` | Cutoff-safe selection of pinned local frames, windows, masks and provenance. |
| `observed/motion/geometry.py` | Bounded common ground grid, source resampling, contour polygons and topology-preserving display copies. |
| `observed/motion/tracking.py` | Source-independent matching primitive and source-specific descriptors; independent radar/cloud tracks. |
| `observed/motion/association.py` | Time-aligned radar/cloud/rain-rate/lightning context; no hazard classifier. |
| `observed/motion/route.py` | Continuous legs, signed closure and planned-position intersection under translation. |
| `observed/motion/validation.py` | Source/product/domain registration evidence gate, distinct from matching diagnostics. |
| `observed/motion/payload.py` | Orchestration, status, size limits and deterministic serialization. |
| Pack publication helper | Revision reservation and conditional atomic motion publication; shared by full/realtime artifact writers. |
| Web / Swift adapters | Tolerant contract decoding, freshness/expiry presentation, layer ownership and interaction. |

Names identify proposed ownership, not files that already exist. Extend existing
frame/route/cache primitives where appropriate rather than duplicating them. No
database migration is required. Small publication-control files inside a pack are
permitted; a second public prediction data stream is not.

### Dependencies

Use existing NumPy, pyproj, HDF5/netCDF readers and Pydantic. Declare SciPy directly
(`>=1.10`, subject to the repository's installation tests); the repository already
uses it and MetPy requires it transitively. Add Shapely `>=2.0` for polygon unions,
holes, intersections and translations instead of implementing a polygon engine.
Verify supported Python/wheel combinations during implementation; neither package
compatibility nor performance is asserted by this spec. Do not add pysteps,
OpenCV, scikit-image, GDAL or rasterio in this increment.

An established optical-flow library remains a later comparison candidate. It does
not remove the masking, registration, lifecycle or validation requirements of this
baseline.

## 3. Version-1 policy: explicit experimental choices

These values make the implementation reproducible and bounded. They are **not
calibrated meteorological thresholds, physical speed limits or safety margins**.
They must be emitted as `policy_version` plus their relevant footprint definition;
changes require a policy-version change, updated tests and a replay comparison.

| Item | Initial value / behavior |
|---|---|
| Capability | `WB_OBSERVED_MOTION_ENABLED` off by default; also requires `WB_OBSERVED_ENABLED`. User prediction-mode opt-in defaults off. |
| Primary history | Latest usable contiguous suffix, at most 4 frames/source, at least 3 distinct valid times; total span at most 45 minutes. |
| Adjacent gap | At most two configured source cadences: DBZH 10 minutes, CTTH 20 minutes. Retain actual elapsed seconds. |
| Current-motion freshness | Latest reference age at cutoff at most 20 minutes; this is distinct from projection expiry. Older observed evidence is historical only. |
| Analysis grid | Route-centred azimuthal-equidistant ground grid; 2 km cells. Version 1 supports the current OPERA grid with absolute source spacing 2 km on both axes; other radar spacings are explicitly unsupported, not silently resampled into a new resolution policy. |
| Domain cap | At most 262,144 ground cells; neither dimension above 1,024; no ground point more than 1,000 km from the projection centre. Do not coarsen automatically. |
| History source decoding | CTTH at most 46 full-width rows and 262,144 source cells per decode block; reduce block rows as needed, or reject an unsupported width. Process/release blocks sequentially. Radar source windows at most 1,048,576 cells and neither dimension above 2,048, checked before decode; stream/release one frame at a time. |
| Candidate feature definitions | Radar detected returns at or above 5 dBZ; cloud successful retrievals at or above 4,572 m (15,000 geometric ft MSL). These are explicit inspection contours, not entire weather-system boundaries. |
| Track size | At least 9 connected ground cells; smaller detections remain observation evidence but do not receive motion. |
| Candidate work | At most 32 candidates/source; 64 forward and 64 reverse correlation patches/source/frame-pair. |
| Template | 31 × 31 cells; fixed supported mask covers at least 80% of the template and at least 64 samples. Exactly two usable separated patches/feature/pair are required for acceptance. |
| Search displacement | At most 60 m/s × actual adjacent-frame interval. A peak at the search boundary is rejected, not reported as a capped velocity. |
| Match diagnostics | NCC at least 0.80; best competing peak outside a 2-cell neighbourhood at least 0.10 lower; nonconstant template/target. |
| Geometric match | Reciprocal one-to-one match; translated intersection-over-union at least 0.50 on common known support. |
| Lineage ambiguity | More than one candidate with translated overlap at least 20% of the smaller footprint means ambiguous/split/merge; withhold propagation. |
| Consistency | Reverse error at most one cell diagonal; next-observation residual at most two cell diagonals; common-support area ratio within [2/3, 3/2]. |
| Research projection cap | Reference observation time + 15 minutes. Age/receipt latency consumes this interval; never reset it from request/device time. |
| Advertised times | At most three future absolute UTC 5-minute ticks, strictly after cutoff and no later than an eligible feature's projection end. |
| Route approximation | Preserve every waypoint/bend; densify using the existing great-circle convention to at most 1 NM segments; cap 2,048 segments, otherwise withhold route calculations. |
| Concurrent analysis | At most one optional motion computation per application process; busy admission returns explicit unavailability, not an unbounded queue. |
| Execution budget | Cooperative 15-second compute budget, checked between bounded blocks/patches/stages. This is not a hard wall-time or RSS guarantee. |

Contour choices intentionally leave weaker echoes and lower cloud outside this
tracking experiment. Display the definitions and untracked/omitted counts; never
infer clear weather outside the selected contours. Existing observed displays are
not filtered or reclassified by these choices.

Pad the latest-feature capture region around the largest existing 20 NM route
corridor by the maximum 15-minute search displacement (54 km), then add the
selected history's maximum displacement and template half-width for analysis
support. CTTH additionally needs source-window padding for supplied parallax.
This is an algorithmic search region, not a promise nothing outside it can arrive.
If the bounds exceed a cap, return `region_too_large`; do not silently omit route
ends or replace missing coverage with zero.

## 4. Time, source selection and registration

Capture an aware-UTC `cutoff_at` before source selection. Both nominal valid time
and actual receipt time must be no later than cutoff. Missing/malformed receipt
time makes a frame ineligible for motion; observation-only behavior is unchanged.
Pin source ID, product/decoder identity, nominal timestamp, grid descriptor,
acquisition window, receipt time and a content identity. Verify selected identities
still match after reading; disappearance/replacement yields explicit unavailability,
not an unnoticed substitution with a later frame.

Validate actual acquisition metadata as well: `acquisition_start <= acquisition_end
<= received_at <= cutoff_at`, and `valid_at <= received_at`. Reject malformed or
inconsistent intervals; a rounded nominal filename cannot admit an acquisition
ending after cutoff. Use the documented product's **nominal valid/target time**
as canonical `reference_at` for each primary frame, track fitting and the 15-minute
expiry. Its acquisition interval remains separate; neither receipt nor acquisition
midpoint replaces that reference. Missing defensible primary acquisition metadata
is unavailable, not an invented precise interval. Existing observation behavior
and explicitly documented provider interval semantics are unchanged.

Do not call `FrameStore.latest(now=cutoff)` as an as-of selector: it has no upper
time bound and does not check receipt time. Extend the shared frame-store API with
a separately tested as-of selection primitive while preserving existing callers.
Reject non-increasing/duplicate times, inconsistent grids and incompatible product
versions. Start the suffix at the newest eligible primary frame and stop at the
first temporal/grid/readability barrier; do not skip a bad middle frame to make a
plausible-looking sequence. A missing nominal publication can leave a gap up to
the two-cadence limit, recorded explicitly. A known but corrupt, incompatible or
unverifiable frame is a barrier even if bridging it would fit that gap limit.
Future valid/receipt frames are outside the as-of inventory, never gap fillers.
Use three/four observations, not a claim of independent
radar samples: OPERA contributing-scan windows overlap and some inputs are already
extrapolated.

The run has a computation timestamp, not a synthetic common observation time.
Every feature, scalar value, flash/window and aligned association retains its own
source times. For replay, historical files downloaded later cannot be assigned
invented historical receipt times. Separate an as-received evaluation from a
valid-time-only research experiment with unknown delivery latency.

### Source registration gate

Separate `geolocation.status = validated | unverified | failed` from motion-fit
status. "Validated" means a recorded registration check applies to the exact
product/grid/decoder/domain; it does not certify forecast skill. Store evidence ID,
method/version, applicability and measured registration checks in a reviewed
validation manifest. No environment variable or client confidence slider can
manufacture such evidence.

Radar uses its documented ODIM ground grid plus structural/coordinate checks for
the experimental baseline. CTTH has an additional unresolved supplied-parallax
validation requirement. Until a real-product/domain record passes independent
review, it may expose observed cloud contours and candidate image-matching
diagnostics, but **claimed ground speed/bearing, cloud route relations/projections,
and quantitative cross-source associations are unavailable** with
`geolocation_unverified`. Do not publish candidate displacements as knots.

Tests may inject a clearly synthetic validated registration context; production
must never accept a synthetic evidence record. A missing CTTH validation record
is an explicit remaining user requirement, not a silently waived gate.

## 5. Analysis geometry, descriptors and tracks

### Common ground representation

Build the same route-centred metric grid for both families, independent of radar
availability. Sample radar by nearest source cell; do not average/interpolate dBZ
or derive rain rate from it. Maintain separate detected, explicitly undetected
and unknown masks throughout.

For CTTH, transform each source pixel's four corners and supplied centre parallax
offset to ground coordinates, applying that pixel's offset to its corners. The
constant-within-cell correction is part of the registration method requiring
validation. Sample destination **cell centres inside corrected quadrilaterals**,
not their bounding rectangles. Where retrieved footprints overlap, the highest
geometric top wins together with its own temperature, quality and source-sample
identity. No interpolation across differing winning samples.

Cloud detections take precedence over nominal clear samples. A destination without
a cloud retrieval may be known-clear only from explicit, noncontradictory clear
source support; otherwise it remains unknown. Never infer clear sky from an
unfilled destination or a failed/unprocessed source pixel. Retain support and
collision diagnostics. Stream CTTH in bounded blocks with one open granule and
bounded corner-projection batches; do not accumulate all decoded blocks in memory.

Analysis footprints are unions of occupied 2 km grid cells. They are exact relative
to **that discrete contour definition**, not exact cloud/rain boundaries or an
area-conservative sky-coverage measurement. Preserve interior holes and disconnected
parts using Shapely unions of row runs. Do not replace them with convex hulls or
bounding boxes. Source registration and discretization limitations travel with
every derived spatial result.

### Source-specific descriptors

- Radar: use the bounded descriptor `clip((dBZ - 5) / 60, 0, 1)` on valid returns;
  explicit undetect maps to the descriptor's background class. This is matching
  texture, not a physical reflectivity resampling or rainfall conversion.
- CTTH: use the **binary high-top-contour membership descriptor** (retrieved top
  at least 4,572 m). Known lower cloud and explicit clear support are background;
  unknown remains masked. Its zero means "not in this high-top contour", **not
  a measured cloud top at zero metres or proof of clear sky**. Height/temperature
  remain separately retained observations. A height-texture or multi-channel
  tracker is not added implicitly; it would need its own policy and comparison.

This separate categorical CTTH descriptor allows isolated high-cloud boundaries
to be matched without inventing zero-height values in clear pixels. Broad uniform
regions without identifiable boundaries/texture remain observed-only.

### Matching sequence

1. Label eight-connected components of each defined contour independently.
   Retain observed detections, including those too small or clipped for tracking.
   Rank bounded candidates by current contour distance to the route, then area
   descending, then stable grid order. Ranking is work selection, not severity;
   omitted counts are visible and no route-wide absence statement is permitted.
2. Pick two usable high-variance template centres per candidate, separated by at
   least two grid cells; fewer than two means motion unavailable. Rank by descriptor
   variance, then stable row/column order for ties. For each adjacent frame pair
   use normalized correlation over a **fixed supported mask**: reference-known template pixels intersected
   with target-known support under every tested displacement. Do not let competing
   shifts choose different missing-data subsets. Unknown values may not contribute
   to correlation, including indirectly through a moving coverage edge.
3. Apply the policy's support, texture, peak and search-edge checks. Require both
   patches to agree within one cell diagonal. Attempt quadratic refinement when
   all nine local 3 × 3 peak scores are finite and inside the search region.
   Accept it only for a negative-definite peak with the fitted offset within half
   a cell of the integer solution; otherwise use the integer displacement and
   record that resolution. Combine the two forward displacements by their
   arithmetic mean. Apply the same two-patch checks in reverse at the proposed
   corresponding target locations; reverse consistency compares the forward mean
   with the negated reverse mean. Do not add the reverse estimate to the fitted
   forward displacement. These diagnostics/refinements are not uncertainty calibration.
4. Translate the earlier contour using the accepted patch displacement; build
   lineage candidates separately from final matches. Multiple plausible parents
   or children mean split/merge/ambiguity even if one has the largest score. Require
   reciprocal uniqueness, common-support IoU and the area-change checks before
   extending a track. Do not convert a failed match into a zero velocity.
5. Require a clean chain of at least three frames **ending at the newest reference
   observation**. Never project an old clean chain onto a newly unmatched feature.
   For adjacent accepted displacements `d01`, `d12` over `dt01`, `dt12`, test the
   next-observation residual `norm(d12 - d01 * dt12 / dt01)` against the policy
   limit, before fitting with `d12`. This is an earlier-pair extrapolation check,
   not a centroid residual or an in-sample final-fit residual. A fourth frame adds
   the analogous successive-triple check. The latest clean three-frame suffix can
   be used if only the oldest pair fails; failure of the newest pair cannot be
   bypassed. Fit translation against actual
   elapsed times from cumulative **matched displacements**, not movement of a
   changing contour's centroid. Fit x/y independently by least squares; retain
   pair-to-pair variation and residuals.
6. Anchor projection to the latest unsimplified observed contour. Translate it
   rigidly; do not expand it from recent growth or preserve a split parent's vector
   on its children. New children need a new clean track. Observed changes are
   measured only over common known support and are not predictions of growth.

Feature-level support is mandatory in addition to template checks. For an accepted
pair displacement `d`, let `K0`, `K1` be unions of known source-support ground
cells, `A = F0 + d`, `B = F1`, and `H = (K0 + d) intersect K1`. Both `A` and `B`,
including a one-cell square rim around each, must lie within `H` and the analysis
domain. Otherwise the feature is coverage/domain-clipped and cannot propagate,
even if its interior patches correlate well. Retain its positive observed portion
with the clipping reason. Compute common-support IoU and area ratio from
`A intersect H` and `B intersect H` with these continuous polygon operations after
any fractional displacement; do not round masks back to whole cells.

Label the complete bounded contour field **before** minimum-size, 32-candidate or
serialization selection. Lineage checks consider all relevant labeled components,
including small/unselected ones, under the proposed pair translation. A second
parent/child meeting the overlap rule prevents a unique match even if it would
never get a displayed track. Component-label/count masks can reject ambiguous
matches without polygonizing every omitted object. If the complete relevant
lineage check cannot finish within the budget, report `lineage_not_evaluated`;
never accept uniqueness from a truncated competitor list.

Track IDs are run-scoped. Timed trail samples mark observed feature centres and
their source frames, not physical air parcels. The fitted translation, trail and
appearance-change diagnostics have distinct meanings. Report accepted zero speed
as **"no resolved movement"**, with bearing null; missing/ambiguous speed is null
and explicitly unavailable. Direction is **toward**, in degrees true clockwise
from north, not meteorological wind-from direction. Ground speed is in knots.
These Earth-relative quantities are representative at the latest contour's area
centroid, not simply `atan2(vx, vy)` of off-centre AEQD axes. Inverse-project that
centroid and its position after one second of the fitted grid translation; use
the WGS84 geodesic distance/one second and forward azimuth, recording that reference
point/method. A zero grid vector has zero speed and null bearing. Keep the grid
vector for contour/route calculations; local speed/direction can vary across a
large rigidly translated projected shape, which is another model limitation.

## 6. Associations and lightning precision

Never merge radar and cloud track identities. For two tracks with validated ground
registration and usable common history, compare at the latest time inside both
observed track intervals. Use actual observed contours when a frame exists at
that time; otherwise translate the preceding contour within its accepted
bracketing interval and mark the comparison as motion-aligned. Do not morph
vertices, extrapolate beyond observed history to create an association, or call
the derived alignment a simultaneous observation.

Record intersection area, each contour's overlap share and edge distance, explicitly
as **analysis-grid contour measurements**, not true sky-area percentages. Any
nonempty area overlap produces an `overlap` candidate. Disjoint contours within
one grid-cell diagonal produce a `nearby` candidate, not an assertion of a common
storm. Links are many-to-many; ambiguity is retained. Selection highlights both
tracks, including different directions/speeds. Without compatible time, support or
registration, supply the reason and independently timed context, not a link.

Rain rate is optional observed context. Choose a RATE frame whose actual source
time lies inside the radar track's observed interval; align the echo contour to
that time and sample RATE only over known support. Otherwise rain-rate association
is unavailable. Keep its units, acquisition window, coverage and time distinct.
Only positive matched rate supports a rain-context label; the radar object remains
typed `radar_echo`. No rate estimate is derived from dBZ.

### Lightning

Extend `FlashFrame`/the reader with per-detection time precision and reason metadata.
Currently `_flash_times` and a length-mismatch fallback substitute the granule end
time. Preserve positions and backward-compatible observation behavior, but motion
logic must distinguish `individual_time` from `window_only`. Missing, masked,
invalid, mismatched or out-of-window times cannot silently become precise events.

For a genuine individual time, compare its observed position to eligible feature
geometry at that time inside accepted history. An event inside a contour is
positive electrification context for that feature, not storm classification. A
window-only detection remains separately marked regional context; it is not
assigned to a precisely timed feature. Neither type is advected into the future.

Validate finite coordinates and source intervals. Scope IDs by frame/sample;
count **reported detections**, not necessarily distinct physical flashes across
overlapping files. Preserve source/detection time precision in all cards and
counts. Zero associated detections is not absence of thunderstorms, and LI has
no complete-coverage guarantee in the existing point payload.

Aggregate each retained feature's lightning evidence **before** marker selection:
reported-detection count, original source frames/evaluated window, evaluation status
and completeness. Preserve that summary independently of the 256 displayed-marker
cap. A known positive feature must retain positive evidence on its card even if
none of its detections is selected for the map. Window-only regional detections
never enter this precisely associated count; partial evaluation retains known
positive lower-bound counts but cannot yield an evaluated zero/no-match claim.

## 7. Route relationships and time projection

Use named original route legs, including every bend and repeated waypoint name;
leg IDs include their index and route identity. Extend/reuse the common route
walker for dense geometry without changing its existing default sampling behavior
or unrelated advisory calculations. Maintain the same great-circle distance basis
as planned route timing. Degenerate zero-length legs have no passage interval;
they must not create a division by zero or erase other valid legs.

For an accepted feature with contour `F0`, velocity `v` and reference `t0`, use
`F(t) = F0 + v * (t - t0)`. Its supported future interval begins at cutoff and
ends at `t0 + 15 minutes`; if empty, no future projection exists. Keep the contour
and its required support inside the analysis domain. Leaving it gives
`outside_analysis_domain`, not a clipped partial contour reported as complete.

For each eligible leg and evaluation time:

- Compute the minimum distance between the **full contour** and the continuous
  leg, not the distance between a centroid and a sampled waypoint. Intersection
  means distance zero; nearest boundary distance while inside is not an escape
  clearance.
- Closure is `-(d(t_b) - d(t_a)) / (t_b - t_a)`, converted to knots. Use a centred
  60-second interval where supported, shortened at the reference/expiry boundaries;
  carry both interval endpoints. Positive means distance decreasing. An intersecting
  contour has closure not applicable rather than automatically zero. Distinguish
  approaching, receding, approximately unchanged (absolute closure below 1 knot,
  a display-resolution rule), intersecting and unavailable.
- Carry distance/closure uncertainty qualifiers and the specific leg. Do not
  present one signed distance as the relationship to the whole bent route.
- The 5/10/20 NM corridor selector only compares these distances with a selected
  proximity radius. It is not position uncertainty, a storm-separation standard
  or a safe passage buffer.

### Planned aircraft overlap

Use departure plus duration × cumulative-distance fraction, matching existing
planning semantics. Require aware departure, finite positive duration, consistent
positive route distance and a matching route/timing identity. Do not substitute a
generic aircraft speed, the live GPS symbol, a cruise-wind estimate or departure
when those inputs are invalid. If the planned flight interval does not intersect
the feature's supported interval, report `outside_planned_interval`.

Compute continuous overlap under this model, not just at the three UI ticks.
On each timed dense route segment intersect the **relative aircraft segment**
`p(t) - v * (t - t0)` with the static contour `F0`, restricted to the overlapping
planned/projection interval. Preserve polygon holes, multiple intersections and
tangent instants. For a zero-length relative segment, the whole tested interval
overlaps only if the contour covers that point. Convert each intersection's
segment fraction back to UTC. Use unsimplified analysis geometry.

These are horizontal, constant-shape, planned-timing overlap intervals, not a
forecast of physical entry into a storm or a vertical hazard assessment. Report
minute-rounded intervals as approximate; do not publish second-precise encounter
countdowns. Empty intervals mean only "no overlap calculated for this tracked
contour under this model in the evaluated interval"; never "route clear". If the
solver hits a resource/geometry limit, return unavailable, not an empty result.

Keep per-tick `planned_overlap_at_time` distinct from the continuous interval
result. Missing rows or unsampled times alone cannot establish no encounter.

## 8. Version-1 wire contract

The [normative record definitions](2026-09-05-observed-motion-contract.md) fix the
field names, types, enums and nullability for Python, TypeScript and Swift. They
are part of this written specification, not an implemented schema. The summary
below explains the intent; implementations must share fixtures for those records.

All dates are aware ISO-8601 UTC. All numerical values are finite or explicitly
null. Use arrays with ID **values**, not dictionaries keyed by source/feature IDs:
Swift's global snake-case decoder can transform dictionary keys. Identifiers are
opaque strings; unknown optional fields are preserved in raw cached motion JSON
and ignored for rendering.

### Envelope

| Field | Meaning |
|---|---|
| `schema_version` | 1; unsupported versions disable only motion rendering. |
| `status` | `disabled`, `unavailable`, `available`. Availability is not full coverage or validated skill. |
| `reason_codes` | Stable codes; nonempty for disabled/unavailable. |
| `revision` | Positive integer, monotonic within a pack; max JavaScript-safe integer. Includes failed/disabled publications. |
| `run_id` | Content identity of selected inputs, method, policy, registration context and route/timing; null if no computation occurred. Not an ordering token. |
| `route_geometry_id`, `planned_timing_id` | Exact bindings; timing ID is null if timing is unusable. |
| `computed_at`, `cutoff_at`, `expires_at` | Calculation, selection cutoff and last supported projection end; expiry is nullable when no projection exists. None is a shared observation timestamp. |
| `method_version`, `policy_version` | `masked_contour_translation_v1` and `observed_motion_policy_v1`. |
| `analysis_domain` | Geographic bounds, ground CRS/cell size, grid dimensions and coverage/registration limitations. |
| `sources`, `features`, `associations`, `lightning` | Arrays with source, feature, link and detection records. |
| `projection_times` | Sorted unique absolute UTC times. A feature may be unavailable at some advertised times. |
| `completeness` | Explicit counts/flags for excluded regions, candidates, small detections, features, geometry, links, lightning, legs and overlap intervals. |

`available` means at least one accepted motion result can be inspected, including
one with no remaining future lead. Source and per-feature states still apply.
`unavailable` can retain explicitly observed-only features/context, but no accepted
motion/projection is represented as current. `disabled` has no active geometry.
Client clock-derived expiry is separate from this stored scientific result state.

Each source records source ID, status/reasons, selected frames and gaps, valid and
receipt times, actual acquisition start/end, attribution, coverage, and registration
status/evidence. Source absence and no detected features are distinct.

Each feature records run-scoped ID, family, contour definition, reference source
time, frame references, observed `display_geometry`, trail samples, separately
timed scalar observations, motion status/reasons, nullable speed/bearing, raw
matching diagnostics, registration status, projection end, per-time projected
geometry/status, per-leg/time rows and planned-overlap interval/status records.
Per-feature lightning evidence survives marker truncation independently.
No top-level confidence percentage is emitted. Lineage/ambiguity reasons are
retained even when no velocity is accepted.

Association rows reference two features in the same run, relation/status/reasons,
comparison time/method, original source times/windows and supported contour
measurements. A dangling or cross-run reference cannot render as an association.
Lightning rows carry original position, event time or acquisition window, precision,
source frame reference and nullable arrays of associated feature IDs.

Per-leg/time rows include leg ID/index/labels, evaluation time, distance NM,
nullable signed closure knots, closure interval, relationship and reasons,
planned-time method and nullable planned overlap at that instant. Continuous
overlap results separately carry evaluated interval, method/status and approximate
overlap intervals. Null/omitted means not evaluated/unavailable, never a negative.

### Geometry and payload bounds

Use GeoJSON-compatible `MultiPolygon`: each polygon is an exterior ring followed
by its holes, using `[longitude, latitude]` pairs. Rings are closed, finite,
non-self-intersecting and within geographic bounds. Preserve topology, interior
holes and disconnected components; convert coordinate order exactly once at each
Leaflet/MapKit boundary. Unsupported topology is explicit unavailability, never a
filled bounding-box substitute.

`display_geometry` is a topology-preserving simplified copy with at most 1 km
deviation from the grid contour. Carry that tolerance and `grid_contour` provenance.
Scientific association/closure/overlap uses the unsimplified contour. If the display
copy cannot meet limits without breaking topology, leave its geometry unavailable
but keep the feature in the accessible list with its reason.

| Serialization limit | Cap |
|---|---:|
| Features | 48 total: first up to 24/family, then unused slots may fill from the other family's bounded candidates. |
| Trail samples | 4/feature. |
| Projection times | 3. |
| Positions per footprint | 128 total across all rings/components, including closing positions. |
| Components/holes | 8 polygon components, 8 holes total per footprint. |
| Total geometry positions | 12,000 across all observed/projected footprints. |
| Association links | 128. |
| Displayed lightning detections | 256. |
| Leg/time rows | 1,024. |
| Continuous overlap intervals | 256. |
| Serialized motion JSON | 1 MiB UTF-8, uncompressed. |

Use deterministic route-proximity/grid-order selection, preserve each available
family's initial allocation, and emit exact known omission counts/reasons. Prefer
retaining feature status/cards over detailed geometry. Do not silently increase
simplification error. If the envelope cannot fit even with bounded geometry/detail
removed, replace it with `unavailable: payload_limit`. Unselected legs/intervals
are not negative results, and truncation forbids any route-wide absence summary.

## 9. Publication, refresh and persistence

### Ordering is not a timestamp comparison

A scientific `run_id` can repeat when input frames repeat. It cannot order a newer
failure against an older success. Reserve a monotonic pack-scoped `revision` at
the start of each attempted motion update, including disabled/unavailable outcomes.
Use a small pack-identity-keyed `observed-motion-state-<pack-id>.json` control
sidecar in the pack's parent directory with atomic replacement; initialize from
at least the existing snapshot revision. Protect it with a **separate stable lock
file** keyed by the same exact identity. Do not lock the replaceable control-file
inode or unlink the lock during ordinary publication/pack deletion. Preserve the
state sidecar's revision high-water mark across deletion/recreation of the same
public flight/pack identity. A recreated generation starts strictly above that
mark, never again at revision 1; clients can therefore order old and new responses
without guessing an ordering of opaque generation IDs. If the high-water mark is
lost, that deleted public identity cannot be reused. No revision is derived from
the wall clock. Exhaustion of the safe integer range is explicit failure, not
wraparound.

The control record also binds an immutable pack-generation token. The full-pack
writer alone can authorize first creation, reserving that generation and revision
under the lock before computation. If the snapshot does not yet exist, its first
atomic publication uses that writer's complete base snapshot and the same current
generation; a refresh cannot use this path. Existing packs initialize the control
record only after verifying their existing metadata/snapshot identity. Pack deletion
participates in the same lock and invalidates the generation without deleting its
revision high-water mark. Every later commit
rechecks both generation and pack existence without recreating directories; an
old computation cannot resurrect a deleted pack or write into a recreated one.

Compute outside the publication lock. Publish only if the reserved revision is
still the newest attempted revision and the route/timing identity still matches.
Under the lock, reread current snapshot JSON and atomically replace only the motion
field and the intended refreshed fields, preserving unrelated/unknown content.
A superseded computation must not overwrite or return itself as the latest result;
its caller receives the currently published motion state, or an explicit transport
failure if no current state can be read. A newer failed attempt publishes its own
unavailable envelope. A crash leaves the old published run with its original age,
never relabelled as newly computed; a later attempt advances the reserved revision.

All same-pack snapshot writers must preserve this field's revision ordering.
Full pipeline/artifact writes to a reused pack, legacy snapshot writes and direct
refreshes cannot bypass the publication helper and restore an older motion block.
Use atomic snapshot replacement so concurrent snapshot/bundle readers cannot see
half-written JSON. A genuinely different public pack identity has its own revision
namespace; a reused flight/pack URL retains its monotonic counter. Do not alter
unrelated advisory/model calculations as part of this durability change.

These are new ordering guarantees for the motion field, not a claim that every
other existing pack field becomes transactionally versioned.

### Complete integration surface

Update all of the following, not only the main refresh button:

- Full briefing assembly in `pipeline.py`, `ForecastSnapshot`,
  `tasks/artifacts.py::save_analysis_artifacts` and legacy snapshot persistence.
- `tasks/route_weather.py::run_realtime_refresh`, including explicit failed,
  disabled and outside-D-0 results from the optional motion stage.
- `RealtimeRefreshResult`, `RefreshAccepted`, gated POST, SSE-complete and direct
  `/{timestamp}/observations/refresh` responses, using `observed_motion` consistently.
- Existing snapshot and bundle reads; the bundle contains the same full motion
  block and the normal offline pack completeness requirements remain unchanged.
- Web API/store types, direct-refresh merge, ordinary snapshot reload, selected
  pack/route invalidation and map orchestration.
- Swift `SnapshotResponse`, `RefreshEvent`, `BriefingViewModel`, repository/cache
  patching and `AppIntents/RefreshDriver` before it reports offline-save success.

The source collector remains the only provider-access path. No weather provider
requests are added to motion analysis, and no computation runs on a client. Admit
one optional computation per process; report busy/deadline/cap failures explicitly
and preserve the ordinary observation briefing. Cooperative deadlines do not stop
an in-progress native HDF5/FFT call. Verify actual memory/time before enabling this
on a shared deployment; do not advertise the cooperative budget as a hard timeout.

No cross-run motion-result cache is added in this increment. Reusing stored analysis
for inspection is distinct from recomputation. Frame decoding remains streamed;
there is no decoded full-granule cache. A later result-cache optimization needs its
own complete-input key, bounded size and tests of eligibility/expiry/publication
ordering; it is not hidden implementation scope here.

### Serve-time capability and compatibility

New snapshot, bundle and refresh responses carry
`X-Observed-Motion-Enabled: 0|1`, based on current server capability, not the stored
pack's envelope. Both clients consume it through their network boundary. Older
responses cannot overwrite a newer capability observation from a later request.
Serve capability-bearing responses with `Cache-Control: no-store`, and ensure
browser/native cache policy does not satisfy the capability check from HTTP cache.
The existing application's deliberate offline pack cache remains supported.

Capability authority is process/session state, never a persisted `enabled = true`
permission. It starts **unknown** on app/page restart, prediction-mode entry,
foreground return and reconnect. Before active prediction presentation, make one
bounded, authenticated GET to the **existing snapshot endpoint**, bypassing caches,
with a 10-second transport deadline; coalesce an already in-flight check for the
same flight/pack/request generation. This read does not run motion analysis or
request weather from a provider. Apply its motion block through normal revision
ordering as well. Unknown/missing authority, timeout or network failure allows
only explicitly stored-analysis inspection; it cannot authorize active styling.

This lifecycle check is the sole extra automatic read introduced here. It is not
polling: clock ticks, time/feature/source selections and redraws make no requests.
Later ordinary snapshot/bundle/refresh responses can update authority. A continuously
open session learns server disable only at its next server contact; no instantaneous
remote-revocation guarantee is claimed. Each projection still expires at its own
source-based time regardless of remembered capability.

When online capability is 0, remove active motion overlays regardless of a stored
available envelope. The next applicable refresh publishes disabled state. If the
header or motion block is absent on an older server/pack, present unsupported or
"refresh needed", not clear weather or a freshly recomputed cached run. An offline
client cannot learn a subsequent server switch-off; its cached data is always
presented as stored analysis, never newly authorized live availability.

Missing/null motion on a legacy payload means no new motion update; it cannot make
a retained run count as refreshed. Explicit disabled/unavailable envelopes are
full replacements, including empty arrays, not nullable fields to skip. Whole
request/network failure is different from a completed motion-stage failure: retain
the previous run only as its original dated analysis and report refresh failure.

### Client ordering and raw caching

Compare revisions on **disk writes as well as screen updates**. An older same-pack
bundle/snapshot response cannot replace a newer realtime motion envelope, even
when that newer result is unavailable. Do not merge feature arrays across runs.
An identical revision is idempotent; conflicting content for the same revision
is a contract error, not permission to choose a visually nicer result. Request
generations additionally guard navigation and capability changes.

Swift needs a small raw-motion JSON wrapper with tolerant typed access. Preserve
the raw block for cache round trips, including unknown optional fields, while
known version-1 data drives the view. Unknown schema/status or malformed motion
disables that feature, not decoding of the whole briefing or METAR/TAF response.
The web follows the same tolerant boundary. Do not reconstruct the cached raw
block solely from today's known DTO fields.

For malformed data whose ordering cannot be established, report motion invalid
for the current request; do not stamp an older valid run as current. It can remain
historical cached evidence. Where a valid newer revision is readable, the newer
unsupported raw block supersedes the old one for rendering. Never use an invalid
schema's guessed feature geometry or scalar values.

Extend `BriefingCacheStore.patchRealtimeSnapshot` and snapshot/bundle writes through
the same revision-aware raw-JSON merge in the cache actor. Preserve unknown root
fields, account for byte-count changes and keep atomic protected writes. Realtime
patching must not recreate deleted or partial packs. Save failure remains visible
to in-app and Siri callers; online data may be shown with an explicit offline-save
error but cannot be described as saved. Target the initiating flight + pack, not
whatever history entry is selected when the request finishes.

## 10. Web and native iOS interaction

Both clients deliver the same capabilities; a DTO-only iOS change is not completion.
Use a dedicated **Experimental motion** mode, initially off. Enter it in Observed
view with both available feature families enabled and no feature selected.
Unavailable source controls stay visible with a reason. Retain the user's normal
observed-raster preference for returning to that view; do not silently change it.

The prediction mode shows:

- Distinct radar and high-top outlines, using line style and labels as well as
  colour. Outline labels disclose the 5 dBZ and 15,000 ft MSL contour definitions.
- Short observed trails at their own source times. The selected projection time
  uses dashed outlines and an explicit **experimental constant-motion projection**
  legend. No probability cone or calibrated uncertainty fill is invented.
- A keyboard/screen-reader-accessible feature list and selection card, usable
  even when geometry is too complex or native basemap tiles are unavailable.
- Ground speed/bearing and separate per-leg distance/closure; correctly paired
  top height/temperature and separately timed rain/flash evidence; support,
  registration, lineage, missing-data and truncation warnings.
- A source-timed association selection highlighting both contours without hiding
  different vectors. Lightning stays at observed positions/times and is visually
  distinguishable when only its acquisition window is known.
- A selected-feature route-leg/time table and approximate planned-overlap intervals.
  These are analysis results, not an aircraft alarm, route verdict or clearance.

Show only Observed or one selected projection time's geometry, not every future
polygon simultaneously. Projection controls list the server's absolute UTC times,
not a client-generated "+15 minutes from now". A feature unavailable at a selected
time shows that reason; its old observed polygon is not held over as the prediction
for that time. No selection, clock tick, source toggle or map redraw fetches weather
or reruns analysis. The mode-entry/lifecycle capability read in §9 is the explicit
exception to no automatic network reads, and cannot recompute motion. Ordinary
weather refresh remains an explicit action.

For web, own prediction overlays in a separate Leaflet layer group, including its
legend and selection lifecycle. For iOS, separate route-owned and weather-owned
MapKit overlays before adding polygons: current `updateRoute` removes all overlays.
Render holes with interior polygons/even-odd fill. Metric recolouring, aircraft
symbol updates and source selection must not erase or duplicate weather layers.
No raw-raster underlay is requested in this mode on either platform.

### Expiry, navigation and offline state

On a newer revision or different run/pack/route, clear obsolete selection and reset
to Observed. Never carry a feature ID or relative lead choice across runs. Reject
late callbacks after navigation or map destruction; preserve the existing map URL
ownership discipline for the separate ordinary-raster view.

Recompute only presentation ages/expiry on minute ticks and foreground/visibility
changes. A past selected projection time, expired feature or invalid clock cannot
remain labelled as a current future prediction. Hide its active projected geometry
and explain the expired selection while leaving the controls focused/usable; do
not silently advance to another projection time. If every advertised time has
expired, offer Observed/stored analysis and explicit refresh.

Use UTC dates as well as times for old packs. When the device clock precedes the
server cutoff/reference unexpectedly, disable current-age/future claims and show
clock uncertainty. Do not clamp future timestamps to "0 minutes old".

Offline mode is explicitly **Stored analysis**. Do not reset any reference,
cutoff or lead-time origin. Current-prediction styling is unavailable offline;
the user can deliberately inspect stored observations/projections with their
absolute times and limitations. No basemap-tile availability is promised. Geometry
and cards are optional views of the cached snapshot, not a new offline-completeness
requirement or automatic raster download.

## 11. Verification required by the implementation plan

All tests below are future prediction work. Existing correction-test counts do
not verify this specification.

### Backend and shared contract fixtures

- Known translations in all directions, speed/unit/bearing convention including
  off-centre AEQD conversion, accepted zero movement versus unavailable speed,
  deterministic subpixel refinement/mean displacement and quantization.
- Independent/opposing radar and cloud movement, cloud-only/radar-only scenes,
  weaker/lower features outside the disclosed contour definitions and small
  untracked detections. Confirm no source's absence suppresses another's evidence.
- Changing nodata masks, explicitly undetected/clear support, constant texture,
  ambiguous peaks, search-boundary solutions, splits/mergers, clipped domains,
  common-support growth/decay and invalid scalar/retrieval combinations.
- Clipped feature boundaries with clean interior patches; fractional-shift support;
  a small/unselected second parent/child; incomplete lineage work; one usable patch
  versus two; an old clean chain followed by a failed newest match; genuine
  earlier-pair next-observation residuals, not in-sample or centroid substitutes.
- Future valid/receipt times, missing receipt metadata, duplicate/non-increasing
  frames, missing publications versus corrupt middle-frame barriers, incompatible
  grids/products and frame replacement during reads. Actual acquisition end beyond
  cutoff cannot pass through a rounded nominal timestamp. Replay must not access
  a frame beyond its cutoff.
- CTTH block-size/open-file bounds, corner registration, highest-top temperature
  association, categorical membership distinct from zero-height/clear-sky claims,
  and a production refusal to accept synthetic geolocation validation.
- Exact-time and interpolated in-history associations, missing common temporal
  support, separate vectors, many-to-many ambiguity and no corridor-max pairing.
- LI genuine versus window-only times, mismatched/masked arrays, out-of-window
  times, duplicate detection accounting and no future advection of flashes.
  More than 256 detections cannot remove known positive evidence from a retained
  feature's card; partially evaluated counts are not falsely shown as zero.
- Full contour/continuous-leg distance including holes and bends; ground speed
  versus closure; present intersection versus closure not applicable; invalid
  planning, repeated/zero-length legs and resource-limit unavailability.
- Continuous planned overlap through an interval between UI ticks, a crossing
  before arrival, tangent contact, multiple/hole-separated intervals and zero
  relative aircraft motion. Verify negative statements are model/interval-scoped.
- Polygon topology/coordinate order, display versus analytical geometry, dangling
  references, finite/null values, unknown versions/statuses, output caps and exact
  omission reporting. Test the 1 MiB cap using serialized UTF-8, not object size.

### Integration, clients and durability

- Full, gated, SSE, direct-refresh and Siri pathways all propagate the same
  `observed_motion` envelope. Ordinary observations survive motion failures.
- Newer unavailable/disabled followed by older ready responses; server concurrent
  computation ordering; atomic publication; cache bundle-versus-realtime races;
  same-revision conflict; request-generation and route/pack mismatch.
- Stable lock inode across control-file replacement; initial full-pack creation
  without a snapshot; deletion/recreation during computation; a refresh cannot
  use the authorized first-creation path or restore a removed pack. Recreated
  same-URL packs advance the durable high-water revision and supersede old cached
  results; delayed responses from the deleted generation cannot regain authority.
- Known/unknown raw motion fields survive atomic cache patching. Malformed motion
  does not fail the whole snapshot. Missing legacy data never becomes a negative
  weather claim. Deleted-pack and save-failure behavior remains correct.
- Live capability disable overrides stored availability consistently for snapshot,
  bundle and refresh responses; older responses cannot restore capability state.
  Reopen a cached-ready iOS pack after restart/server disable: authority starts
  unknown, one fresh existing-snapshot GET is required and no persisted enabled
  value authorizes projection. Check deadline/coalescing, foreground/reconnect,
  HTTP-cache bypass, missing headers and network-failure stored-only fallback.
- Web real-entrypoint browser tests for selection, holes, independent source
  controls, UTC projection selection, expiry, foreground clock correction,
  responsiveness, keyboard focus and layer teardown. Time/feature/source selection,
  clock ticks and map redraws issue zero requests; only the specified lifecycle
  capability read is automatic and it performs no weather fetch/recomputation.
- Swift contract/geometry/view-model/cache tests for the same cases, including
  MapKit overlay ownership and cards without basemap tiles. Static review here;
  compilation/unit/UI/device execution remains deferred until the user restores
  Mac testing. Never report those unexecuted tests as passed.

Use the worktree's own venv and isolated temporary data/SQLite for Python, ordinary
web tests/application typecheck and the existing no-server Chrome harness. Do not
source shared `.env`, touch shared weather/DB data, run `npm run build`, replace a
dev server or use provider credentials as part of isolated software verification.

### Real-source and replay evidence

Keep source registration separate from predictive skill. CTTH approval needs
actual packed/decoded coordinate/offset units and signs, footprint registration
checks against independent geolocation for relevant heights/viewing angles, and
an evidence record tied to product/decoder/domain. Existing large reported
low-cloud offsets are not accepted by assertion. IR effective-cloudiness scaling
and LI coverage limitations from the correction review remain unresolved; neither
is silently repurposed as a confidence channel.

Replay representative regional convective, stratiform, quiet, missing-coverage
and rapidly changing cases against persistence and simple advection. Report
spatial/displacement errors, route-overlap misses/false alarms and timing error by
lead, source availability, lifecycle and coverage. Declare the replay corpus,
receipt-time limitations and observation cutoff. Do not select acceptance limits
by looking at later test frames, and do not redistribute restricted raw granules
in the repository. Public/authorized test-data acquisition is a separate documented
step, not permission to read the user's shared data or credentials silently.

No forecast-skill threshold or operational enablement is approved by this spec.
A later validation report must support any proposed use/horizon before a claim of
usefulness. The 15-minute cap remains a research bound until then.

## 12. Review, rollout and handoff

Keep PR #600 draft and update its existing fork branch after implementation,
verification and independent review. Preserve the correction history. Do not
create a second PR, merge, enable auto-merge, deploy or send a message to Brice
without separate authorization for those actions. Update the PR body only with
features actually implemented and evidence actually obtained.

The final prediction handoff must identify independently:

- Implemented backend/web/native behavior and exact commit/test evidence.
- Source-specific unavailable gates, especially outstanding CTTH registration.
- Experimental policy/lead limitations and the actual replay evidence, if any.
- Mac/iOS execution still deferred unless subsequently performed with permission.
- Optional history/raster/time-chart enhancements still excluded.

Before implementation, self-review this spec for placeholders, contradictions,
scope and ambiguous contracts; commit it locally and request the user's written
spec review. After approval, invoke the writing-plans skill, then implement with
test-first development and independent code review. This document is not an
implementation plan, deployment authorization or evidence that prediction exists.

## References

- [Full-request audit and approved design discussion](../../../designs/reviews/2026-09-05-observed-request-coverage.md).
- [PR #584 correctness review and source references](../../../designs/reviews/2026-09-05-pr584-observed-review.md).
- [As-built observed conditions](../../../designs/current-conditions.md).
- [Meteorology decisions](../../../designs/meteorology-decisions.md), especially
  the echo-top/cloud-top distinction and geometry/extent semantics. Existing NWP
  convective thresholds are not imported as observed storm classifications.
- [iOS cache/data models](../../../designs/ios-app-data-models.md) and
  [server contract](../../../designs/ios-app-server-api.md).
