# Observed motion: version-1 record definitions

Date: 2026-09-05. Normative companion to the
[motion design](2026-09-05-observed-motion-design.md), approved for implementation with it.
This is a wire-format specification, **not an implemented API or test result**.
Algorithm, limits, publication and UI rules remain in that design.

## Conventions

Every listed field is required in newly produced version-1 records. `T | null`
means the key is present with an explicitly nullable value. `T[]` is an array,
including an empty array where specified; it is never a map keyed by identifiers.
Quoted alternatives are literal strings. Integers are nonnegative JavaScript-safe
integers unless a stricter range is stated. Numbers are finite. No NaN, infinity,
numeric strings or sentinel values stand in for unavailable measurements.

Reusable types:

| Type | Definition |
|---|---|
| `UTC` | ISO-8601 string with explicit UTC `Z`, including date. |
| `ID` | Nonempty opaque string; never derive identity by changing snake/camel case. |
| `Reasons` | Array of stable reason-code strings. Unknown codes display a generic unavailable/qualified-evidence explanation, not a positive inference. |
| `Availability` | `available` or `unavailable`; unavailable always has nonempty `reason_codes`. Available can have limitation reasons. |
| `Point` | `[longitude, latitude]`, WGS84 degrees, within [-180,180] and [-90,90]. |
| `Interval` | Object with `start_at: UTC`, `end_at: UTC`; start <= end. Equality represents an instant, not a negative-duration interval. |
| `MultiPolygon` | GeoJSON object with `type: "MultiPolygon"`, `coordinates: number[][][][]`; positions are `Point`, rings are closed and topology-preserving under the design's limits. No empty geometry masquerades as available. |
| `Fraction` | Number in [0,1], an analysis-support/contour measurement, never probability. |

Producers validate cross-field rules below. Consumers preserve unknown optional
fields in raw JSON, but do not guess their meaning. An unknown root schema/status
disables motion rendering without failing the briefing. Unknown/malformed nested
records disable their affected geometry/measurement and dependent references;
they cannot produce an accepted result. If envelope identity/ordering itself is
invalid, the entire current motion response is invalid. The design's raw-cache and
same-revision conflict rules apply before any rendering fallback.

## Envelope: `ObservedMotion`

| Field | Type / rule |
|---|---|
| `schema_version` | Integer literal 1. |
| `status` | `disabled`, `unavailable`, `available`. |
| `reason_codes` | `Reasons`; nonempty unless available with no envelope-level limitations. |
| `revision` | Integer in [1, 9007199254740991], public flight/pack-identity-scoped ordering token; its durable high-water mark survives same-identity deletion/recreation. |
| `run_id` | `ID | null`; content identity, not ordering. |
| `route_geometry_id` | `ID`; binds exact route geometry even when analysis is refused. |
| `planned_timing_id` | `ID | null`; null for unusable planned timing. |
| `computed_at`, `cutoff_at` | `UTC`; completion/publication outcome time and selection cutoff, respectively. |
| `expires_at` | `UTC | null`; maximum accepted feature `projection_end_at`, possibly already past. Null if none. |
| `method_version` | String literal `masked_contour_translation_v1`. |
| `policy_version` | String literal `observed_motion_policy_v1`. |
| `analysis_domain` | `AnalysisDomain | null`. |
| `sources` | `SourceRecord[]`. |
| `features` | `FeatureRecord[]`. |
| `associations` | `AssociationRecord[]`. |
| `lightning` | `LightningRecord[]`. |
| `projection_times` | `UTC[]`; sorted unique server-advertised times, bounded as in design. |
| `completeness` | `CompletenessRecord[]`; every category below appears once. |

For `disabled`: run/domain/expiry are null, all data/projection arrays are empty,
and completeness categories are `not_evaluated`, not evaluated zero detections.
Identity, revision, cutoff and outcome time are still required.

For `unavailable`: no accepted motion, future geometry or available route-motion
result is emitted. Observed-only features, source diagnostics and regional lightning
may remain; otherwise the same empty/null shape is valid. A stage that inspected
inputs can have a nonnull run ID even though no motion was accepted.

For `available`: run/domain are nonnull and at least one emitted feature has
accepted motion. Remaining future lead is not required; an expired result can be
inspected as dated analysis. Features unavailable for motion remain distinguishable.
Serialization must not drop the last accepted feature yet keep available status.
Client active/stored/expired presentation state is never stored in this envelope.

## Domain, support and source records

### `AnalysisDomain`

| Field | Type / rule |
|---|---|
| `center` | `Point`, chosen projection centre. |
| `crs` | String containing the exact pyproj-compatible WGS84 AEQD definition. |
| `cell_size_m` | Number literal 2000. |
| `width_cells`, `height_cells` | Positive integers within design limits. |
| `origin_x_m`, `origin_y_m` | Lower-left ground-grid corner coordinates; x eastward/y northward in the projected grid. |
| `bounds` | `[west, south, east, north]`, geographic extent, finite ordered limits. A wrapping/unsupported extent is refused, not silently reordered. |
| `reason_codes` | `Reasons`, including projection/discretization limitations. |

### `SupportRecord`

| Field | Type / rule |
|---|---|
| `status`, `reason_codes` | `Availability`, `Reasons`. |
| `scope` | `analysis_domain`, `feature_contour`, `match_template`, or `point_detections`. |
| `known_cells`, `total_cells` | Integer or null; known <= total when both known. |
| `known_fraction` | `Fraction | null`; null for unavailable/zero denominator or point-only coverage. |

Point detections do not acquire complete area coverage from their bounding box.
Counts describe the stated discrete analysis scope, not sky-area coverage.

### `GeolocationRecord`

| Field | Type / rule |
|---|---|
| `status` | `validated`, `unverified`, or `failed`. |
| `reason_codes` | `Reasons`; nonempty unless validated. |
| `evidence_id`, `method_version`, `applicability_id` | `ID | null`; all nonnull when validated. |

The record references reviewed evidence; it does not embed a user-supplied approval.
The manifest identifies product, grid, decoder and domain applicability. Production
cannot accept synthetic evidence. Ground radar structural checks and CTTH's real
parallax-registration gate have the distinct meanings in the design.

### `SourceRecord`

| Field | Type / rule |
|---|---|
| `source_id` | `ID`; includes exactly the existing primary/context sources considered, without using IDs as object keys. |
| `status`, `reason_codes` | `Availability`, `Reasons`; describes usable source input, not whether motion was accepted. |
| `frames` | `FrameRecord[]`, ordered by canonical valid time. |
| `gaps` | `FrameGap[]`, ordered by earlier time. |
| `attribution` | String, source/product credit. |
| `coverage` | `SupportRecord`. |
| `geolocation` | `GeolocationRecord`. |

### `FrameRecord` and `FrameGap`

| Frame field | Type / rule |
|---|---|
| `frame_id`, `content_id`, `product_id`, `decoder_version`, `grid_id` | `ID`; pin exact selected inputs. No local filesystem path or credential is included. |
| `valid_at`, `received_at` | `UTC`, satisfying cutoff/receipt rules. |
| `acquisition_window` | `Interval`, actual/documented source interval. |
| `reference_at` | `UTC`; equals canonical `valid_at`, not receipt/window midpoint. |

`FrameGap` has `from_frame_id: ID`, `to_frame_id: ID`,
`elapsed_seconds: number` (positive), `missing_nominal_publications: integer`,
and `reason_codes: Reasons`. A corrupt barrier is represented in source reasons,
not smuggled into an accepted sequence as an ordinary gap.

Primary histories use at most four frames. Context RATE and LI selection also
uses at most four eligible frames/source, newest first before chronological
serialization; disclose any omitted context. RATE evidence selects the latest
eligible frame inside each radar track's observed interval. LI selects windows
intersecting the considered observed histories; no flash-window extrapolation is
introduced when history is absent. Context cannot bypass cutoff or resource limits.

## Features and observations

### `FeatureRecord`

| Field | Type / rule |
|---|---|
| `feature_id`, `source_id` | `ID`; run-scoped feature identity and owning source. |
| `family` | `radar_echo` or `high_cloud_top`. |
| `definition` | `ContourDefinition`. |
| `reference_at` | `UTC`; newest/reference observation, including for observed-only features. |
| `reference_frame_id` | `ID`; resolves to this source's frame. |
| `frame_ids` | `ID[]`, used observed history in chronological order. |
| `display_geometry` | `GeometryRecord`, including explicit geometry-unavailable state. |
| `trail` | `TrailSample[]`, observed centres with source times, max four. |
| `observations` | `ScalarObservation[]`, separately timed source context. |
| `lightning_evidence` | `FeatureLightningEvidence`, retained independently of map-marker selection. |
| `coverage` | `SupportRecord`. |
| `geolocation` | `GeolocationRecord`; must agree with applicable source evidence. |
| `motion` | `MotionRecord`. |
| `projection_end_at` | `UTC | null`; reference +15 minutes for accepted motion, otherwise null. |
| `projections` | `ProjectionRecord[]`; one per envelope time, including unavailable entries. |
| `route_rows` | `RouteRow[]`, deterministic bounded leg/time results. |
| `planned_overlap` | `PlannedOverlapResult`. |
| `reason_codes` | `Reasons`, including small/omitted/clipped or other observation limitations. |

`ContourDefinition` has `quantity: "reflectivity" | "geometric_cloud_top_height"`,
`operator: "gte"`, `threshold: number`, `unit: "dBZ" | "m_msl"`.
The valid pairs are radar 5 dBZ and cloud 4572 m MSL. Neither is a storm threshold.

`GeometryRecord` has `status: Availability`, `reason_codes: Reasons`,
`geometry: MultiPolygon | null`, `provenance: "grid_contour"`,
`simplification_tolerance_m: number` (0 to 1000). Available requires nonnull valid
geometry; unavailable requires null, never an empty/fill-in bounding rectangle.
It describes a display copy, not the unsimplified server-analysis geometry.

`TrailSample` has `frame_id: ID`, `observed_at: UTC`, `center: Point`.
These are contour centres, not matched parcels or future positions.

### `ScalarObservation`

| Field | Type / rule |
|---|---|
| `kind` | `reflectivity_max`, `rain_rate_max`, or `cloud_top_max`. |
| `status`, `reason_codes` | `Availability`, `Reasons`. |
| `value` | Number or null; null when unavailable. |
| `unit` | Respectively `dBZ`, `mm_h`, or `m_msl`. |
| `source_id`, `frame_id` | `ID | null`; required nonnull when available. |
| `observed_at`, `comparison_at` | `UTC | null`; actual scalar observation time and feature-alignment time. Nonnull when available. |
| `acquisition_window` | `Interval | null`; nonnull when available. |
| `alignment_method` | `observed` or `in_history_translation` or null. |
| `sample_id` | `ID | null`; winning sample identity, nonnull when available. |
| `sample_position` | `Point | null`; winning sample's ground position when available/registered. |
| `paired_temperature_k` | Number or null; only the temperature of the same winning cloud-top sample. Null for other kinds or missing valid temperature. |
| `coverage` | `SupportRecord`. |

These extrema belong to this feature's supported footprint at the comparison time,
not unrelated corridor maxima. Height can be available while its temperature is
null, with a reason. Radar reflectivity can be negative in other observations but
these contours start at the policy threshold. Rain rates are nonnegative; only a
positive matched observed rate supports the rain-context wording. No dBZ conversion.

## Motion and projection records

### `MotionRecord`

| Field | Type / rule |
|---|---|
| `status` | `accepted` or `unavailable`. |
| `reason_codes` | `Reasons`; nonempty when unavailable. |
| `ground_speed_kt` | Nonnegative number or null; null when unavailable, accepted zero is meaningful. |
| `bearing_deg_true` | Number in [0,360) or null; null when unavailable or zero movement. |
| `velocity_reference_point` | `Point | null`; latest contour centroid for Earth-relative conversion. |
| `velocity_method` | `inverse_aeqd_geodesic_1s` or null; nonnull when accepted. |
| `pair_diagnostics` | `PairDiagnostics[]`, max three. |
| `fit_rms_residual_cells` | Nonnegative number or null; in-sample fitting diagnostic, never held-out error or probability. |

Unverified cloud geolocation cannot coexist with accepted motion or published ground
speed/bearing. Candidate image-matching diagnostics may remain in grid-cell units.
The authoritative projected vector stays on the server; clients never derive
projection positions from the representative speed/bearing.

### `PairDiagnostics` and `PatchDiagnostics`

`PairDiagnostics` fields are:

- `from_frame_id: ID`, `to_frame_id: ID`, `elapsed_seconds: number` (positive);
- `status: Availability`, `reason_codes: Reasons`;
- `patches: PatchDiagnostics[]` (two forward and two reverse entries for a fully
  evaluated accepted pair; partial diagnostics can have fewer, never more than four);
- `forward_dx_cells: number | null`, `forward_dy_cells: number | null` (means of
  the two accepted forward estimates);
- `patch_disagreement_cells: number | null`, `reverse_residual_cells: number | null`,
  `next_observation_residual_cells: number | null` (nonnegative; the last is null
  when there is no subsequent pair to test, not a zero-error claim);
- `common_support_iou: Fraction | null`, `area_ratio: number | null` (positive);
- `plausible_parent_count: integer | null`, `plausible_child_count: integer | null`,
  `lineage_complete: boolean`.

`PatchDiagnostics` has `direction: "forward" | "reverse"`,
`center_column: integer`, `center_row: integer`, `status: Availability`,
`reason_codes: Reasons`, `support_fraction: Fraction | null`,
`ncc: number | null` ([-1,1]), `competing_peak_margin: number | null`,
`dx_cells: number | null`, `dy_cells: number | null`, and
`refinement: "quadratic" | "integer" | null`. These are raw matching diagnostics,
not confidence scores. Pixel-row orientation is explicitly converted to projected
y when fitting; diagnostic dx/dy use the domain's east/north grid axes.

### `ProjectionRecord`

Fields are `at: UTC`, `status: Availability`, `reason_codes: Reasons`, and
`display_geometry: GeometryRecord`. `at` must be in the envelope's advertised
times. An available projection must have accepted motion, valid geolocation,
`cutoff_at < at <= projection_end_at`, full domain support and available geometry.
A geometry-limited projection remains unavailable visually even if numerical route
calculations using its valid analytical contour are independently available.
An unavailable entry never carries its older observed polygon as a prediction.

## Radar/cloud associations and lightning

### `FeatureLightningEvidence`

| Field | Type / rule |
|---|---|
| `status`, `reason_codes` | `Availability`, `Reasons`. |
| `source_id` | `ID | null`; nonnull for evaluated evidence. |
| `frame_ids` | `ID[]`; original LI source frames considered for this feature. |
| `evaluated_window` | `Interval | null`; enclosing evaluated times within accepted observed history. Original frame windows/gaps remain authoritative, not a continuous-coverage claim. |
| `reported_detection_count` | Integer or null; count of positively associated individually timed reported detections, including those omitted from the map. |
| `emitted_marker_count` | Integer; how many of those detections survive serialization in the envelope's marker array. |
| `evaluation_complete` | Boolean; completeness only for the stated local inputs/evaluated window, never a complete lightning-coverage guarantee. |

Build this summary before applying the marker cap. Available requires a nonnull
count and evaluated window: a positive count preserves evidence even if evaluation
is partial, in which case it is explicitly a lower bound with a reason. Zero is
allowed only after complete evaluation of the declared compatible local inputs.
Unavailable requires a null count and nonempty reasons, not an apparent no-match.
Missing sources, incompatible timing/registration or only window-timed detections
cannot establish an evaluated zero. `emitted_marker_count` can be zero while the
reported count is positive and must not exceed it. No feature card derives its
positive-evidence label solely from the capped marker array. Counts refer to reported
detections; duplicate physical flashes across source files remain possible.

### `AssociationRecord`

| Field | Type / rule |
|---|---|
| `association_id`, `radar_feature_id`, `cloud_feature_id` | `ID`; two resolvable, appropriate-family features in this run. |
| `status`, `reason_codes` | `Availability`, `Reasons`. |
| `relation` | `overlap`, `nearby`, or null; null when unavailable. |
| `comparison_at` | `UTC | null`; nonnull when available. |
| `alignment_method` | `simultaneous_observed`, `in_history_translation`, or null. |
| `radar_frame_ids`, `cloud_frame_ids` | `ID[]`; exact/bracketing frame references. |
| `radar_window`, `cloud_window` | `Interval | null`; enclosing original acquisition support, not a claim of continuous observation. |
| `intersection_area_km2` | Nonnegative number or null. |
| `radar_overlap_fraction`, `cloud_overlap_fraction` | `Fraction | null`. |
| `edge_distance_nm` | Nonnegative number or null. |
| `measurement_basis` | Literal `analysis_grid_contours`. |

Available requires all comparison fields and measurements, compatible history,
support and registration. Nearby permits zero intersection, not false overlap.
Unavailable has null relation/time/method/measurements; original frame/window
references can remain diagnostic context. An empty/capped association array is
never proof that no storms or physical associations exist.

### `LightningRecord`

| Field | Type / rule |
|---|---|
| `detection_id`, `source_id`, `frame_id` | `ID`; sample-scoped reported detection, not cross-file flash identity. |
| `position` | `Point`, observed; never advected. |
| `time_precision` | `individual_time` or `window_only`. |
| `event_at` | `UTC | null`; nonnull only for a validated individual event time inside its acquisition window. |
| `acquisition_window` | `Interval`. |
| `reason_codes` | `Reasons`, including the cause of a window-only fallback. |
| `association_status` | `Availability`, for evaluation against eligible inspected features. |
| `association_reason_codes` | `Reasons`; nonempty when association unavailable. |
| `associated_feature_ids` | `ID[] | null`; null when association unavailable/window-only. |

An available empty feature-ID array means no association among evaluated compatible
contours, not no thunderstorms. Omitted evaluations are disclosed. All references
must resolve after output selection; removing a target must not convert a positive
association into an evaluated negative. Preserve the detection with an unavailable
association reason or remove it with an explicit omission count.

## Route and planned-overlap results

### `RouteRow`

| Field | Type / rule |
|---|---|
| `leg_id` | `ID`, includes index and route identity. |
| `leg_index` | Integer, zero-based original leg index. |
| `from_label`, `to_label` | Strings; repeated names do not merge identities. |
| `at` | `UTC`; reference time or an advertised projection time. |
| `status`, `reason_codes` | `Availability`, `Reasons`, for distance/relationship. |
| `distance_nm` | Nonnegative number or null; zero for intersection, null if unavailable. |
| `closure_kt` | Signed number or null; positive toward this leg. Null for intersection/unavailable. |
| `closure_interval` | `Interval | null`; nonzero duration when closure is available. |
| `relationship` | `approaching`, `receding`, `approximately_unchanged`, `intersecting`, or `unavailable`. |
| `planned_time_method` | Literal `distance_proportional_planned`. |
| `planned_time_status`, `planned_time_reason_codes` | `Availability`, `Reasons`; independent from distance availability. |
| `planned_overlap_at_time` | Boolean or null; evaluated for the planned position on this leg at this instant only. Null for invalid timing, another leg's passage, or unsupported time. |

Display-resolution closure classification is in the design. The row is tied to a
specific leg; a closure value never stands for the whole bent route. Units, grid
contour/projection limitations and the distinction from ground speed stay visible.

### `PlannedOverlapResult` and `OverlapInterval`

`PlannedOverlapResult` has `status: Availability`, `reason_codes: Reasons`,
`method: "relative_segment_contour_intersection"`,
`planned_time_method: "distance_proportional_planned"`,
`evaluated_interval: Interval | null`, `intervals: OverlapInterval[]`,
and `complete: boolean`.

`OverlapInterval` has `leg_id: ID`, `leg_index: integer`,
`start_at: UTC`, `end_at: UTC`, `contact: "interval" | "tangent"`,
and `approximate: true`. Endpoints are rounded outward to minute boundaries for
display intervals (floor entry/ceil exit), clamped to the evaluated interval;
tangent instants are rounded to the nearest minute within that interval and keep
the tangent label. Solver calculations are not rounded. Rounding may touch/overlap
adjacent displayed intervals; do not imply second-level separation from that.

Available means the whole stated evaluated interval was evaluated, with complete
valid planned timing, geometry and resource support. `complete` is true and the
evaluated interval is nonnull; an empty array then has only the model-scoped negative
meaning stated in the design. Unavailable requires reasons, `complete: false` and
empty intervals, not a partial list that appears complete. The tested interval can
remain for diagnostics; otherwise it is null. Capping intervals makes this result
unavailable and records omissions in completeness. Results are per feature, never
a route-wide clearance or vertical assessment.

## Completeness and reason codes

`CompletenessRecord` has `category: string`,
`status: "complete" | "partial" | "not_evaluated"`, `reason_codes: Reasons`,
`considered_count: integer | null`, `emitted_count: integer`, and
`omitted_count: integer | null`. Unknown counts are null, never fabricated zeros.
When both counts are known, considered = emitted + omitted for that category.

Required categories: `regions`, `input_frames`, `small_detections`, `candidates`,
`features`, `geometry`, `associations`, `lightning`, `legs`, `route_rows`,
`overlap_intervals`. Counts are category-specific, not additive across categories.
Small detections distinguish observation evidence from accepted tracks. Limits,
unsupported region/coverage and unevaluated work must prevent absence summaries.

Version-1 reasons are lower_snake_case and are not translated on the wire. Initial
codes are:

- Capability/input: `feature_disabled`, `observed_disabled`, `outside_d0`,
  `missing_source`, `insufficient_history`, `stale_reference`, `missing_receipt_time`,
  `invalid_time`, `future_acquisition`, `history_gap`, `unreadable_frame`,
  `incompatible_grid`, `incompatible_product`, `frame_changed`, `missing_acquisition`.
- Geometry/matching: `region_too_large`, `unsupported_grid_spacing`,
  `source_window_limit`, `unknown_support`, `coverage_clipped`, `outside_analysis_domain`,
  `small_feature`, `insufficient_patches`, `constant_texture`, `ambiguous_peak`,
  `search_boundary`, `patch_disagreement`, `reverse_inconsistent`,
  `next_observation_inconsistent`, `nonreciprocal_match`, `split_merge_ambiguous`,
  `lineage_not_evaluated`, `area_change`, `overlap_insufficient`, `invalid_geometry`,
  `geometry_limit`, `grid_discretization`, `projected_translation`.
- Evidence/time: `geolocation_unverified`, `geolocation_failed`,
  `no_common_history`, `no_detected_features`, `no_matching_rate`,
  `temperature_unavailable`, `window_only_time`, `invalid_flash_time`,
  `out_of_window_time`, `time_array_mismatch`, `point_coverage_unknown`,
  `no_eligible_feature`, `no_future_lead`, `unsupported_time`.
- Planning/resources: `invalid_route`, `invalid_planned_timing`,
  `outside_planned_interval`, `outside_leg_interval`, `degenerate_leg`,
  `route_segment_limit`, `lineage_work_limit`, `busy`, `compute_deadline`,
  `compute_failed`, `payload_limit`, `selection_limit`, `reference_omitted`,
  `overlap_interval_limit`, `not_evaluated`.

Additional diagnostic reason strings are forward-compatible only if they cannot
change interpretation of existing status/measurement fields; a changed method,
threshold, enum or negative-result meaning needs the relevant version update.
Client-only states such as `capability_unknown`, `unsupported_schema`,
`refresh_failed`, `clock_uncertain`, `stored_analysis` and `expired` are local
presentation states, not rewrites of the stored scientific result.
