# Issue #223 Meteorology and Metrics Audit

| ID | Area | Current behaviour | Intended contract | Evidence | Impact | Planned/existing test at discovery | Initial disposition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A223-01 | Turbulence | Sounding exists but vertical_motion is absent; point votes clear | Missing CAT/vertical-motion structure is unavailable, never smooth | Code path plus designs/meteorology-approach-review-2026-06.md §3.6 | High safety | test_turbulence_missing_vertical_motion_is_unavailable | blocker |
| A223-02 | Aggregation | Empty/all-unavailable input becomes GREEN | Empty/all-unavailable becomes UNAVAILABLE | Direct model helper inspection | High safety | test_empty_and_all_unavailable_aggregate_unavailable | blocker |
| A223-03 | Registry | Evaluator exception is logged and result disappears | Preserve advisory as unavailable with diagnostics | Direct registry inspection | Medium contract | test_registry_exception_returns_unavailable_result | blocker |
| A223-04 | Airport | Missing airport domain returns empty per_model | Emit one unavailable result per requested model | Direct evaluator inspection | Medium contract | test_missing_airport_domain_is_explicitly_unavailable | blocker |
| A223-05 | Distance | affected_nm is point-count proportion | Use midpoint-owned route cells and union geometry | Objective geometry contract | Medium display | test_uneven_route_midpoint_cells | direct correction |
| A223-06 | SFIP display | Map colours use 20/50/80 | Use backend/catalog 15/30/55 | sfip_to_risk and tests/test_sfip.py | Medium contract | route-map SFIP boundary tests | blocker |
| A223-07 | SFIP no-vv | Missing omega retains dead 10%/15% weight | Normalize remaining weights; do not alter thresholds | Fuzzy weights sum and existing design review | Medium model-vote bias | no-vv normalization tests | direct correction |
| A223-08 | DD/NWP clouds | Pairwise intersection double-counts overlaps; Jaccard can exceed 1 | Merge each interval set and intersect the unions | Set geometry identity | Medium diagnostic | overlapping-layer Jaccard test | direct correction |
| A223-09 | Airport gust vector | Crosswind and gust graded separately | No change without POH/standard/observation evidence | Meteorology approach review §3.8 | Calibration-dependent | characterization test | evidence-dependent follow-up |
| A223-10 | DD okta labels | Vertical saturation is treated as horizontal coverage | No threshold/label recalibration without observations | Meteorology decisions §1 and approach review §3.4 | Calibration-dependent | Existing cloud tests | evidence-dependent follow-up |
| A223-11 | Ri/SLD/resolution | Potential method and resolution improvements | Validate/document only unless an oracle or observations support a change | Design reviews | Calibration-dependent | No regression test: no behaviour change is authorized without a qualifying oracle or observations | evidence-dependent follow-up |
| A223-12 | Convective character | `convective_character.py` is an explanatory characterization path, not the active advisory grade predicate | Verify units, inputs, and separation from `ConvectiveEvaluator`; make no calibration change without qualifying evidence | Direct code/design comparison; inspected symbols and tests recorded in A223-24 | Review found no active-severity or unit contract defect | Existing characterization and below-base tests | no change |
| A223-13 | Ogimet-NWP icing availability | `_resolve_analyses` can place an empty Ogimet-NWP zone list in the active slot when `nwp_cloud_layers is None`; evaluators can count it as assessed clear | Native NWP cloud geometry is a required input for Ogimet-NWP; absent geometry is unavailable, while an available empty list is assessed clear | `designs/analysis-metrics.md` Ogimet-NWP requirements plus direct code path | High missing-versus-clear | test_ogimet_nwp_without_native_cloud_geometry_is_unavailable | blocker |
| A223-14 | Convective aggregate attribution | `ConvectiveEvaluator` replaces the representative model's detail with a cross-model percentage range while aggregate mitigations still come from one model | `aggregate_detail`, `aggregate_mitigations`, and `representative_model` must identify the same per-model source | Approved #223 backend contract plus direct override block | Medium contract | test_convective_aggregate_detail_is_representative_model_owned | blocker |
| A223-15 | Scenario/alternate assessment | `derive_assessment_from_advisories` filters unavailable advisories and returns GREEN when nothing valid remains | An empty/all-unavailable advisory picture is UNAVAILABLE, not a clear scenario | Direct helper inspection and missing-data policy | High safety/display | test_derive_assessment_all_unavailable_is_unavailable | blocker |
| A223-16 | `cloud_top.py` | `highest_top + margin_ft > flight_ceiling_ft` grades only soundings that exist; a missing route-point sounding reduces the denominator and a remaining clear subset can stay GREEN | Preserve the established 1000 ft margin and 25%/60% cut-points, but a partial result that would otherwise look clear is UNAVAILABLE; derive grade, extent, and evidence from the same reachable-layer predicate | `CloudTopEvaluator.evaluate`; approved design §5.3; existing cirrus/reachable-top tests | High missing-versus-clear; thresholds unchanged | `test_cloud_top_partial_clear_becomes_unavailable`; existing `test_tops_above_ceiling` and `test_ignores_cirrus_above_ceiling` | blocker |
| A223-17 | `vmc_cruise.py` | BKN/OVC layers containing cruise are counted, but missing route-point soundings drop out of `total`, so a partial clear subset can remain GREEN | Preserve BKN 25% amber and OVC 50% red thresholds; missing points make a clear result unavailable while available hazard evidence survives | `VMCCruiseEvaluator.evaluate`; approved design §§5.1, 5.3; `designs/advisories.md` | High missing-versus-clear; no recalibration | `test_cloud_partial_clear_becomes_unavailable`, `test_cloud_partial_hazard_preserves_amber`, existing `test_red_ovc_at_cruise` | blocker |
| A223-18 | `icing_escape.py` | `has_relevant_icing` plus freezing-level-versus-terrain escape logic is internally consistent, but an unavailable selected Ogimet-NWP method can arrive as an empty active zone list and vote clear | Keep terrain, tight-margin, altitude-buffer, coverage, and no-escape predicates unchanged; gate evaluation on selected-method availability and preserve exact icing-zone geometry | `_resolve_analyses`; `IcingEscapeEvaluator.evaluate`; meteorology decisions §8; A223-13 | High missing-versus-clear; no threshold change | `test_ogimet_nwp_without_native_cloud_geometry_is_unavailable`; existing `test_no_escape_is_red` and `test_high_altitude_icing_ignored` | blocker |
| A223-19 | `fiki_icing.py` | `_transit_icing` sums clipped zone thickness and cruise clearance, but `zones == []` is treated as fully clear even when the selected Ogimet-NWP method is structurally unavailable | Keep transit-thickness, severe/SLD, proximity, and cruise-clearance rules; distinguish an available empty zone set from an unavailable method before grading | `_transit_icing`, `_min_icing_clearance`, A223-13, approved design missing-data policy | High missing-versus-clear; certification-sensitive but no calibration change | `test_ogimet_nwp_without_native_cloud_geometry_is_unavailable`, `test_fiki_single_point_sld_is_red_and_not_diluted`, existing FIKI tests | blocker |
| A223-20 | `freezing_precip.py` | Any active FZRA/PL is RED and warm-nose extent is AMBER, but points with neither precipitation nor derived-level signal remain in the denominator once any other point has a signal | Preserve binary active RED and 5% primed threshold; incomplete points must produce partial/unavailable state rather than dilute a clear result | `FreezingPrecipEvaluator.evaluate`; meteorology decisions §9; existing `test_no_precip_data_is_unavailable` | High missing-versus-clear; no threshold change | `test_freezing_precip_missing_signal_is_unavailable_not_clear`; existing active/primed tests | blocker |
| A223-21 | `turbulence.py` | CAT-at-cruise and strong-motion predicates run only when `vertical_motion` exists, but every sounding increments `total`; an all-missing model becomes GREEN “smooth” | A point is assessed only with a non-UNAVAILABLE vertical-motion assessment; partial severe evidence remains hazard-bearing | `TurbulenceEvaluator.evaluate`; approach review §3.6; A223-01 | High safety | `test_turbulence_missing_vertical_motion_is_unavailable`, `test_turbulence_partial_severe_evidence_remains_red` | blocker |
| A223-22 | `mountain_wind.py` | Terrain points increment `total` before target wind lookup; if every mountain-point wind is missing, `max_wind == 0` yields GREEN | Complete elevation can establish “no mountains”; mountain points require target-altitude wind, and missing terrain/wind is incomplete rather than calm | `MountainWindEvaluator.evaluate`; meteorology decisions §11d; approved design missing-data policy | High missing-versus-calm; wave thresholds unchanged | Add `test_mountain_wind_missing_wind_is_unavailable`; retain existing wave-signature tests | blocker |
| A223-23 | `convective.py` | The selected track plus DD floor, top-clearance filter, HIGH-anywhere override, and LOW cap grade correctly, but missing active assessments drop out; the post-aggregation block also replaces representative detail | Keep all #283 meteorological predicates unchanged; missing active points must be partial/unavailable, and aggregate detail must remain owned by the representative model | `ConvectiveEvaluator.evaluate`; meteorology decisions §§4, 14; A223-14 | High missing-versus-clear and medium attribution contract | `test_convective_missing_active_assessment_is_not_clear`, `test_dd_floor_records_compound_method_and_thermo_geometry`, `test_convective_aggregate_detail_is_representative_model_owned` | blocker |
| A223-24 | `convective_character.py` | `native_cp` is compared with the configured showers threshold on the documented one-hour step; `classify_convective_character` grades a separate realized-coverage/avoidability axis and below-base logic only annotates | Keep the one-hour mm/h-to-mm equivalence explicit, prefer native `cp` when present, and keep character separate from `ConvectiveEvaluator` severity; recalibrate only with radar/observation evidence | `_native_or_metpy`, `showers_at_point`, `_vmc_below_base`, `_below_base_geometry`, `classify_convective_character`; meteorology decisions §15 | Calibration-dependent characterization; no active-severity change | Existing `test_character_*`, `test_vmc_below_base_*`, and `test_below_base_*` tests; no new test needed for this no-change audit | no change |
| A223-25 | `airport_wind.py` | `_wind_status` takes the worst of mean crosswind and absolute gust; missing airport domain returns no models, and a missing endpoint can be skipped | Keep the separate crosswind/gust policy until POH/standard/observation evidence exists, but represent the expected departure/arrival domain explicitly and never call absent input calm | `_wind_status`, `AirportWindEvaluator.evaluate`; approach review §3.8; A223-04/A223-09 | Medium contract plus calibration-dependent wind policy | `test_missing_airport_domain_is_explicitly_unavailable`; `test_gust_vector_crosswind_is_not_recalibrated_without_evidence` | blocker |
| A223-26 | `flight_category.py` | Ceiling/visibility use OR thresholds and terminal MODERATE/HIGH+ convection has no coverage dilution; missing airport domain returns no models and absent axes/endpoints can be skipped | Preserve 3000/5 and 1000/3 thresholds and terminal convective rules; require explicit endpoint availability and make partial-clear unavailable | `_classify_conditions`, `_terminal_convective_status`, `FlightCategoryEvaluator.evaluate`; meteorology decisions §11a | High missing-versus-clear; no threshold change | `test_missing_airport_domain_is_explicitly_unavailable`; existing terminal-convective and flight-category tests | blocker |
| A223-27 | `density_altitude.py` | `DA = PA + 118.8 × (T - ISA(PA))` and worst of absolute/delta thresholds is correct; missing airport domain returns no models and one missing endpoint can be omitted | Preserve the equation and 5000/8000 plus 3000/5000 thresholds; explicitly report expected endpoint availability | `compute_density_altitude_ft`, `_classify_da`, `DensityAltitudeEvaluator.evaluate`; existing equation tests | Medium contract; performance calibration unchanged | `test_missing_airport_domain_is_explicitly_unavailable`; existing ISA/hot-high/unavailable tests | blocker |
| A223-28 | `llws.py` | Worst of 0–1 km shear and gust factor is graded; no route analyses returns no models and a missing endpoint can be omitted | Preserve 20/30 kt shear and 15 kt gust-factor thresholds; explicitly report departure/arrival availability and do not infer calm from absent signals | `_classify_llws`, `LLWSEvaluator.evaluate`; meteorology approach review §5.2 and existing tests | Medium contract; no recalibration | Add LLWS no-analysis equivalent of `test_missing_airport_domain_is_explicitly_unavailable`; retain `test_unavailable_without_any_signal` | blocker |
| A223-29 | `models/advisories.py` | `worst([])`, `majority([])`, and all-UNAVAILABLE collapse to GREEN; `ModelAdvisoryResult.build` estimates nautical miles by point fraction; aggregate detail/mitigations have no explicit representative field | Empty/all-unavailable is UNAVAILABLE; distance is midpoint-cell union geometry; detail, mitigations, and representative model are one policy | `AdvisoryStatus`, `ModelAdvisoryResult.build`, `RouteAdvisoryResult.from_per_model`; approved design §§4.3, 5.2, 5.3 | High safety and medium display/attribution contract | `test_empty_and_all_unavailable_aggregate_unavailable`, `test_uneven_route_midpoint_cells`, representative-model tests | blocker |
| A223-30 | `registry.py` | `evaluate_all` logs evaluator exceptions and omits the advisory from results | Return an explicit unavailable advisory with diagnostics so failure cannot masquerade as a missing/clear category | `evaluate_all` exception handler; approved design §5.3 | Medium contract and diagnosability | `test_registry_exception_returns_unavailable_result` | blocker |
| A223-31 | `route-map/metrics.ts` | Most metrics consume backend values/risk labels, but SFIP alone reclassifies scores at 20/50/80 with mismatched legend text | Use authoritative SFIP 15/30/55 boundaries and keep all other metric scales unchanged pending separate evidence | `sfipAtLevel.getColor`, `legendStops`, backend `sfip_to_risk`; A223-06 | Medium backend/display contract | `uses the authoritative backend SFIP thresholds 15/30/55`; existing map-metric tests | direct correction |
| A223-32 | `route-graph/metrics.ts` | Scalar values are direct extracts, but ceiling-DD/NWP values above 5000 ft AGL return `null`, visually conflating “above chart range” with unavailable | Keep meteorological values unthresholded; represent above-scale ceiling distinctly from missing if the graph range/UX is revised | `ROUTE_GRAPH_METRICS` ceiling getters; approach review §4.4; visualization design | Low/medium display ambiguity, not calibration | Existing `route-graph-metrics.test.ts`; add an above-5000-vs-null regression when an affordance is designed | evidence-dependent follow-up |
| A223-33 | Cross-section layer thresholds | Icing, SFIP, SLD, CAT, E-Shear, and convective layers filter backend `risk != none` and map backend severity labels to theme colours; they do not recompute meteorological cut-points | Continue consuming backend risk/geometry without browser-side thresholding; presentation may interpolate geometry but must not invent severity | `sfip-bands.ts`, icing/CAT/SLD layers, `scales.ts`, `compare-zone-access.ts`; visualization design | No threshold mismatch found; later evidence overlays must preserve backend authority | Existing `compare-zone-access`, `tooltip-formatters`, and layer-registry tests; no new threshold test needed | no change |

## Final evidence closure

The discovery table above records the state and intended disposition at audit
time. The closure records below are authoritative for delivery. `None` in a
follow-up field means no follow-up issue is required. Deferred findings list
their verified follow-up issue URLs.

### A223-01

- Commit(s): `c5b58230d0a2366998fa0a63685fca1d4d380d4f`, `81f81e9f3b7717dc52a00ebf59e87a9039792751`
- Exact tests: `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_missing_vertical_motion_is_unavailable`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_partial_severe_evidence_remains_red`
- Final disposition: Resolved.
- Follow-up: None.

### A223-02

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`
- Exact tests: `tests/analysis/advisories/test_evidence_contract.py::test_empty_and_all_unavailable_aggregate_unavailable`; `tests/analysis/advisories/test_aggregation.py::TestAdvisoryStatusMajority::test_all_unavailable_returns_unavailable`; `tests/analysis/advisories/test_aggregation.py::TestAdvisoryStatusWorst::test_empty_returns_unavailable`
- Final disposition: Resolved.
- Follow-up: None.

### A223-03

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`
- Exact tests: `tests/analysis/advisories/test_registry.py::test_registry_exception_returns_unavailable_result`
- Final disposition: Resolved.
- Follow-up: None.

### A223-04

- Commit(s): `ef00bdc5982fadf2ca1a216aa8ebb57f1313ae22`, `bb983fd661f26998a82f98b0e5f62c492c127f33`
- Exact tests: `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_no_airport_conditions`; `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_no_airport_conditions_or_terminal_convection_is_unavailable`; `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_terminal_hazard_survives_missing_airport_domain`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_no_airport_conditions`; `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_missing_airports_returns_each_requested_model_unavailable`; `tests/analysis/advisories/test_context_action_metadata.py::test_llws_missing_route_analysis_returns_each_requested_model_unavailable`
- Final disposition: Resolved. The first commit made a wholly missing airport domain explicit per model. The later clean-room reproduction showed that an early return still discarded independently assessed terminal convection when the top-level airport artifact was absent; `bb983fd` removed that overstatement and preserved the hazard.
- Follow-up: None.

### A223-05

- Commit(s): `ce4bc90afe91c2e2235af82c783f9a3054b57e4f`
- Exact tests: `tests/analysis/advisories/test_evidence.py::test_uneven_route_midpoint_cells`; `tests/analysis/advisories/test_evidence.py::test_isolated_endpoint_owns_a_nonzero_bounded_cell`; `tests/analysis/advisories/test_evidence.py::test_overlapping_reason_regions_do_not_double_count_distance`
- Final disposition: Resolved for the #223 migrated evidence evaluators. Legacy/unmigrated callers of `ModelAdvisoryResult.build()` retain count-proportional distance by compatibility contract.
- Follow-up: None.

### A223-06

- Commit(s): `e71c108fdd36920dc6e579bf59949ad41b0971a9`, `6a01a79a0c22a0f543f22b41f7fb03d074c19934`
- Exact tests: `web/tests/unit/route-map-metrics.test.ts::uses the authoritative backend SFIP thresholds 15/30/55`; `web/tests/unit/route-map-metrics.test.ts::preserves one decimal at boundary-adjacent tooltip values`
- Final disposition: Resolved.
- Follow-up: None.

### A223-07

- Commit(s): `e71c108fdd36920dc6e579bf59949ad41b0971a9`
- Exact tests: `tests/test_sfip.py::test_full_no_vv_renormalizes_remaining_memberships`; `tests/test_sfip.py::test_proxy_no_vv_renormalizes_remaining_memberships`
- Final disposition: Resolved.
- Follow-up: None.

### A223-08

- Commit(s): `e71c108fdd36920dc6e579bf59949ad41b0971a9`, `addff2d5b44f68f4adcf1038ddcde26252f76fce`, `6a01a79a0c22a0f543f22b41f7fb03d074c19934`
- Exact tests: `tests/analysis/advisories/test_dd_nwp_agreement.py::test_cloud_overlap_merges_internal_overlaps_before_jaccard`; `tests/analysis/advisories/test_dd_nwp_agreement.py::test_cloud_overlap_handles_disjoint_unions`; `tests/analysis/advisories/test_dd_nwp_agreement.py::test_cloud_overlap_canonicalizes_nonpositive_spans_before_empty_contract`; `tests/analysis/advisories/test_dd_nwp_agreement.py::test_cloud_overlap_ignores_nonfinite_spans`
- Final disposition: Resolved.
- Follow-up: None.

### A223-09

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`, `e71c108fdd36920dc6e579bf59949ad41b0971a9`, `6a01a79a0c22a0f543f22b41f7fb03d074c19934`, `5f1fdcaa269c628de819760829e8b071b3cc9528`
- Exact tests: `tests/analysis/advisories/test_airport_advisories.py::test_gust_vector_crosswind_is_not_recalibrated_without_evidence`; `tests/analysis/advisories/test_airport_advisories.py::test_public_airport_wind_keeps_gust_and_crosswind_as_separate_axes`
- Final disposition: Deferred — calibration change remains unauthorized without POH/standard/observation evidence.
- Follow-up: https://github.com/roznet/flyfun-weather/issues/385

### A223-10

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: `tests/test_clouds.py::test_coverage_ovc`; `tests/test_clouds.py::test_coverage_bkn`; `tests/test_clouds.py::test_coverage_sct`; `tests/test_clouds.py::test_build_nwp_cloud_layers_coverage_thresholds`
- Final disposition: Deferred — no okta/coverage relabeling is authorized without observations.
- Follow-up: https://github.com/roznet/flyfun-weather/issues/383

### A223-11

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: No new regression by explicit audit requirement; existing characterization is `tests/test_vertical_motion.py::test_cat_risk_from_low_ri` and `tests/analysis/test_sld.py::TestWarmNoseSLD::test_freezing_rain_produces_sld_zone`.
- Final disposition: Deferred — Ri/SLD/resolution calibration needs an independent oracle or observations.
- Follow-up: https://github.com/roznet/flyfun-weather/issues/382

### A223-12

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: `tests/test_convective.py::test_character_none_when_no_convection`; `tests/test_convective.py::test_character_embedded_takes_priority`; `tests/test_convective.py::test_vmc_below_base_false_when_deck_between_cruise_and_base`; `tests/test_convective.py::test_below_base_marginal_emits_altitude_hint`
- Final disposition: Resolved — audited no change; the characterization path remains separate from active severity.
- Follow-up: None.

### A223-13

- Commit(s): `ce4bc90afe91c2e2235af82c783f9a3054b57e4f`, `4f7d2bc18e38e12423a436e1d10c72fd169a9036`
- Exact tests: `tests/analysis/advisories/test_icing_evidence.py::test_ogimet_nwp_without_native_cloud_geometry_is_unavailable`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_ogimet_nwp_missing_native_cloud_geometry_is_unavailable`; `tests/analysis/advisories/test_icing_evidence.py::test_ogimet_nwp_available_empty_geometry_is_assessed_clear`
- Final disposition: Resolved.
- Follow-up: None.

### A223-14

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`, `3ce9f1313c585d56acc30b3727d531a86004b755`
- Exact tests: `tests/analysis/advisories/test_convective_evidence.py::test_aggregate_detail_is_owned_by_representative_model`; `tests/analysis/advisories/test_evaluators.py::TestConvectiveHeadline::test_aggregate_uses_representative_model_detail`; `tests/analysis/advisories/test_evidence_contract.py::test_representative_model_matches_detail_and_mitigations_source`
- Final disposition: Resolved.
- Follow-up: None.

### A223-15

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`
- Exact tests: `tests/analysis/advisories/test_evidence_contract.py::test_derive_assessment_all_unavailable_is_unavailable`
- Final disposition: Resolved.
- Follow-up: None.

### A223-16

- Commit(s): `946cdf91c6991d532d1f9e9d4313a18ac0988fdf`, `2f19431768b0b131a3b2c9c56eb5121dde8ab811`, `4b596f4f43e4081762313656a3f357113c95c536`
- Exact tests: `tests/analysis/advisories/test_cloud_evidence.py::test_cloud_top_partial_clear_becomes_unavailable`; `tests/analysis/advisories/test_evidence.py::test_partial_green_is_guarded_to_unavailable`; `tests/analysis/advisories/test_cloud_evidence.py::test_cloud_top_emits_disconnected_model_specific_regions`; `tests/analysis/advisories/test_cloud_evidence.py::test_all_missing_cloud_model_uses_localized_no_data[cloud_top]`; `tests/analysis/advisories/test_evaluators.py::TestCloudTop::test_tops_above_ceiling`; `tests/analysis/advisories/test_evaluators.py::TestCloudTop::test_ignores_cirrus_above_ceiling`
- Final disposition: Resolved.
- Follow-up: None.

### A223-17

- Commit(s): `946cdf91c6991d532d1f9e9d4313a18ac0988fdf`, `2f19431768b0b131a3b2c9c56eb5121dde8ab811`
- Exact tests: `tests/analysis/advisories/test_cloud_evidence.py::test_cloud_partial_clear_becomes_unavailable`; `tests/analysis/advisories/test_cloud_evidence.py::test_cloud_partial_hazard_preserves_amber`; `tests/analysis/advisories/test_cloud_evidence.py::test_vmc_red_detail_uses_ovc_only_midpoint_extent`; `tests/analysis/advisories/test_evaluators.py::TestVMCCruise::test_red_ovc_at_cruise`
- Final disposition: Resolved.
- Follow-up: None.

### A223-18

- Commit(s): `ce4bc90afe91c2e2235af82c783f9a3054b57e4f`, `4f7d2bc18e38e12423a436e1d10c72fd169a9036`
- Exact tests: `tests/analysis/advisories/test_icing_evidence.py::test_ogimet_nwp_without_native_cloud_geometry_is_unavailable`; `tests/analysis/advisories/test_icing_evidence.py::test_icing_escape_regions_follow_actual_zones_and_route_cells`; `tests/analysis/advisories/test_evaluators.py::TestIcingEscape::test_no_escape_is_red`; `tests/analysis/advisories/test_evaluators.py::TestIcingEscape::test_high_altitude_icing_ignored`
- Final disposition: Resolved.
- Follow-up: None.

### A223-19

- Commit(s): `ce4bc90afe91c2e2235af82c783f9a3054b57e4f`, `4f7d2bc18e38e12423a436e1d10c72fd169a9036`, `9524fd49583d0f0d70f2f8595483f965eccf16df`, `59a76b513322fa120079bed13f2f197aadb70c19`, `02114469503a934ead704a163caa8c06a03f3cb7`
- Exact tests: `tests/analysis/advisories/test_icing_evidence.py::test_fiki_ogimet_nwp_missing_native_cloud_geometry_is_unavailable`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_single_point_sld_is_red_and_not_diluted`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_midroute_single_point_red_hazard_is_not_diluted`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_sld_does_not_raise_unrelated_transit_zone_severity`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_severe_transit_zone_does_not_raise_unrelated_zone_severity`; `tests/analysis/advisories/test_icing_evidence.py::test_fiki_cruise_and_terminal_concern_extent_is_unioned_without_double_counting`; `tests/analysis/advisories/test_evaluators.py::TestFIKIIcing::test_full_route_icing_is_red`
- Final disposition: Resolved. The earlier commits fixed selected-method availability and route-wide SLD/SEVERE grading. The final clean-room pass then exposed two remaining defects: unrelated zones inherited the route trigger severity, and headline extent omitted AMBER/RED terminal concern or could double-count cruise overlap. `02114469` made severity local to each zone and used the unique cruise/terminal affected union.
- Follow-up: None.

### A223-20

- Commit(s): `4f7d2bc18e38e12423a436e1d10c72fd169a9036`, `9524fd49583d0f0d70f2f8595483f965eccf16df`, `61e7d7488c0eb8afcf303bb5d79bb3836c038cde`
- Exact tests: `tests/analysis/advisories/test_freezing_precip.py::test_freezing_precip_missing_signal_is_unavailable_not_clear`; `tests/analysis/advisories/test_freezing_precip.py::test_structurally_insufficient_profile_without_precip_is_unavailable`; `tests/analysis/advisories/test_freezing_precip.py::test_active_freezing_rain_is_red`; `tests/analysis/advisories/test_freezing_precip.py::test_primed_profile_is_amber`; `tests/analysis/advisories/test_freezing_precip.py::test_clear_profile_without_precip_track_is_guarded_unavailable`; `tests/analysis/advisories/test_freezing_precip.py::test_primed_threshold_uses_only_profile_assessed_points`
- Final disposition: Resolved. The post-`main` review additionally reproduced one primed warm-nose profile being diluted below the 5% threshold by precipitation-only points whose profile predicate was not assessable. `61e7d748` makes both the threshold and displayed primed percentage use the profile-assessed domain while retaining the union-based overall evidence fields and partial data state.
- Follow-up: None.

### A223-21

- Commit(s): `c5b58230d0a2366998fa0a63685fca1d4d380d4f`, `81f81e9f3b7717dc52a00ebf59e87a9039792751`, `397377d01b4fa24d2a0699933292c883a75248e6`
- Exact tests: `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_missing_vertical_motion_is_unavailable`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_severe_cat_survives_unavailable_vertical_motion`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_missing_richardson_assessment_is_partial`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_explicit_clear_richardson_is_complete_green`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_partial_severe_evidence_remains_red`; `tests/analysis/advisories/test_turbulence_evidence.py::test_turbulence_severe_detail_uses_only_severe_cat_extent`
- Final disposition: Resolved.
- Follow-up: None.

### A223-22

- Commit(s): `c5b58230d0a2366998fa0a63685fca1d4d380d4f`, `81f81e9f3b7717dc52a00ebf59e87a9039792751`
- Exact tests: `tests/analysis/advisories/test_mountain_wind.py::test_mountain_wind_missing_target_wind_is_unavailable`; `tests/analysis/advisories/test_mountain_wind.py::test_mountain_wind_missing_elevation_is_unavailable`; `tests/analysis/advisories/test_mountain_wind.py::test_mountain_wind_partial_hazard_remains_red`; `tests/analysis/advisories/test_mountain_wind.py::test_mountain_wave_detail_uses_same_point_speed_signature_and_extent`
- Final disposition: Resolved.
- Follow-up: None.

### A223-23

- Commit(s): `3ce9f1313c585d56acc30b3727d531a86004b755`, `e71aed625278c1ef40f526c7b1dcee5057936647`, `183f31bac2f897ddd9b891163309196891229b17`, `61e7d7488c0eb8afcf303bb5d79bb3836c038cde`
- Exact tests: `tests/analysis/advisories/test_convective_evidence.py::test_missing_active_assessments_are_partial_and_not_clear`; `tests/analysis/advisories/test_convective_evidence.py::test_dd_floor_emits_compound_provenance_with_thermo_geometry`; `tests/analysis/advisories/test_convective_evidence.py::test_cape_fallback_floor_remains_thermo_provenance`; `tests/analysis/advisories/test_evidence.py::test_convective_method_id_normalizes_effective_methods_exactly`; `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_cape_fallback_uses_thermo_provenance`; `tests/analysis/advisories/test_convective_evidence.py::test_aggregate_detail_is_owned_by_representative_model`; `tests/analysis/advisories/test_convective_evidence.py::test_dd_floor_primary_when_floor_contributions_raise_route_grade`; `tests/analysis/advisories/test_convective_evidence.py::test_native_nwp_without_thermo_floor_is_partial_not_clear`; `tests/analysis/advisories/test_convective_evidence.py::test_native_nwp_hazard_without_thermo_floor_is_partial_red`; `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_quiet_nwp_does_not_suppress_thermo_high`
- Final disposition: Resolved. The post-`main` review found that the route evaluator alone applied the DD safety floor and that native NWP could be marked complete without the required thermo input. `61e7d748` centralizes effective risk, source geometry, compound provenance, availability, and completeness in one resolver shared by the route, terminal, and IFR consumers. No convective threshold or top-clearance rule changed.
- Follow-up: None.

### A223-24

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: `tests/test_convective.py::test_character_isolated_few_realized_cells`; `tests/test_convective.py::test_character_organized_widespread_with_front`; `tests/test_convective.py::test_vmc_below_base_clear_when_no_deck`; `tests/test_convective.py::test_below_base_unresolved_dominates_mixed_route`
- Final disposition: Resolved — audited no change; characterization remains separate from active severity.
- Follow-up: None.

### A223-25

- Commit(s): `ef00bdc5982fadf2ca1a216aa8ebb57f1313ae22`, `addff2d5b44f68f4adcf1038ddcde26252f76fce`, `5f1fdcaa269c628de819760829e8b071b3cc9528`, `565ecf7af0a4223f2325d50aaf94e255743d0a63`
- Exact tests: `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_no_airport_conditions`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_condition_without_crosswind_or_gust_is_missing_not_calm`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_benign_gust_only_is_partial_unavailable_not_calm`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_crosswind_without_wind_observation_uses_unavailable_detail`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_hazardous_gust_only_preserves_grade_and_provenance`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_observed_calm_without_direction_or_runway_is_complete`; `tests/analysis/advisories/test_airport_advisories.py::TestAirportWindEvaluator::test_equal_crosswind_and_gust_grade_uses_compound_method`; `tests/analysis/advisories/test_airport_advisories.py::test_gust_vector_crosswind_is_not_recalibrated_without_evidence`; `tests/analysis/advisories/test_airport_advisories.py::test_public_airport_wind_keeps_gust_and_crosswind_as_separate_axes`
- Final disposition: Resolved for the missing-data contract; gust-vector recalibration remains deferred under A223-09.
- Follow-up: None here; A223-09 owns https://github.com/roznet/flyfun-weather/issues/385.

### A223-26

- Commit(s): `ef00bdc5982fadf2ca1a216aa8ebb57f1313ae22`, `bf0a78531e25c92137e4566c5b859335216f018b`, `aa470055e890d9fc198c1caee1d8899a4ced7fd5`, `bb983fd661f26998a82f98b0e5f62c492c127f33`, `61e7d7488c0eb8afcf303bb5d79bb3836c038cde`
- Exact tests: `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_missing_condition_sources_with_clear_convection_is_partial`; `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_assessed_clear_ceiling_with_vfr_visibility_is_complete_green`; `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_missing_terminal_convection_with_vfr_conditions_is_partial`; `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_condition_hazard_survives_missing_terminal_convection`; `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_tied_condition_and_convection_use_composite_provenance`; `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_same_terminal_high_and_extreme_methods_tie_at_red`; `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_terminal_hazard_survives_missing_airport_domain`; `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_quiet_nwp_does_not_suppress_terminal_thermo_high`; `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_missing_airport_source_fields_is_partial_unavailable`; `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_quiet_nwp_does_not_suppress_thermo_high`; `web/tests/unit/airport-condition-rendering.test.ts::airport condition ceiling rendering > distinguishes assessed clear from an unassessed null ceiling`; `web/tests/unit/airport-condition-rendering.test.ts::airport condition ceiling rendering > renders an evidence-free condition with a muted category and valid wind`; `web/tests/unit/airport-summary.test.ts::computeSummaryCondition > excludes missing-derived VFR votes from the category summary`
- Final disposition: Resolved. `bf0a7853` established per-axis airport completeness, but the clean-room and post-`main` passes later found remaining IFR-axis, browser missing-versus-clear, terminal-only, controlling-provenance, and DD-floor parity gaps. `aa470055`, `bb983fd`, and `61e7d748` close those without changing flight-category, terminal-radius, or convective thresholds.
- Follow-up: None.

### A223-27

- Commit(s): `ef00bdc5982fadf2ca1a216aa8ebb57f1313ae22`
- Exact tests: `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_missing_airports_returns_each_requested_model_unavailable`; `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_isa_sea_level_is_zero`; `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_hot_high_field`; `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_unavailable_without_temperature`; `tests/analysis/advisories/test_airport_advisories.py::test_density_altitude_unavailable_without_elevation`
- Final disposition: Resolved.
- Follow-up: None.

### A223-28

- Commit(s): `ef00bdc5982fadf2ca1a216aa8ebb57f1313ae22`, `565ecf7af0a4223f2325d50aaf94e255743d0a63`
- Exact tests: `tests/analysis/advisories/test_context_action_metadata.py::test_llws_missing_route_analysis_returns_each_requested_model_unavailable`; `tests/analysis/advisories/test_context_action_metadata.py::test_llws_partial_route_preserves_available_departure_hazard`; `tests/analysis/advisories/test_llws.py::test_unavailable_without_any_signal`; `tests/analysis/advisories/test_llws.py::test_benign_gust_factor_without_shear_is_partial_unavailable`; `tests/analysis/advisories/test_llws.py::test_hazardous_gust_factor_without_shear_preserves_amber`; `tests/analysis/advisories/test_llws.py::test_equal_shear_and_gust_factor_grade_uses_composite_method`
- Final disposition: Resolved.
- Follow-up: None.

### A223-29

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`, `ce4bc90afe91c2e2235af82c783f9a3054b57e4f`
- Exact tests: `tests/analysis/advisories/test_evidence_contract.py::test_empty_and_all_unavailable_aggregate_unavailable`; `tests/analysis/advisories/test_evidence_contract.py::test_representative_model_matches_detail_and_mitigations_source`; `tests/analysis/advisories/test_evidence_contract.py::test_majority_representative_model_matches_majority_detail`; `tests/analysis/advisories/test_evidence.py::test_uneven_route_midpoint_cells`
- Final disposition: Resolved for aggregation/attribution and the #223 migrated evidence geometry. `ModelAdvisoryResult.build()` remains a legacy count-proportional compatibility helper.
- Follow-up: None.

### A223-30

- Commit(s): `4003bb8328d1cc4906763ce3f23038c04732a17a`
- Exact tests: `tests/analysis/advisories/test_registry.py::test_registry_exception_returns_unavailable_result`
- Final disposition: Resolved.
- Follow-up: None.

### A223-31

- Commit(s): `e71c108fdd36920dc6e579bf59949ad41b0971a9`, `6a01a79a0c22a0f543f22b41f7fb03d074c19934`
- Exact tests: `web/tests/unit/route-map-metrics.test.ts::uses the authoritative backend SFIP thresholds 15/30/55`; `web/tests/unit/route-map-metrics.test.ts::preserves one decimal at boundary-adjacent tooltip values`
- Final disposition: Resolved.
- Follow-up: None.

### A223-32

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: `web/tests/unit/route-graph-metrics.test.ts::returns null when AGL exceeds 5000 ft cap`; `web/tests/unit/route-graph-metrics.test.ts::keeps values exactly at 5000 ft AGL`; `web/tests/unit/route-graph-metrics.test.ts::returns null above 5000 ft AGL cap`
- Final disposition: Deferred — the above-scale-versus-unavailable UX remains undesigned; no meteorological value or threshold changed.
- Follow-up: https://github.com/roznet/flyfun-weather/issues/384

### A223-33

- Commit(s): `addff2d5b44f68f4adcf1038ddcde26252f76fce`
- Exact tests: `web/tests/unit/compare-zone-access.test.ts::icing-bands filters out risk=none and exposes severity`; `web/tests/unit/compare-zone-access.test.ts::sfip-bands filters none and exposes risk as severity`; `web/tests/unit/compare-zone-access.test.ts::cat-bands filters none and exposes risk`; `web/tests/unit/tooltip-formatters.test.ts::icing-bands filters none and renders +SLD when sldRisk`; `web/tests/unit/layer-registry.test.ts::all layer ids are unique`
- Final disposition: Resolved — audited no change; visualization continues consuming backend risk/severity rather than recalibrating it.
- Follow-up: None.

## Baseline verification exception

- Starting HEAD: `bac23fdd5e45ef60db4c54a23ade8e804c242767`, rebased onto `origin/main` at `65f596ed`.
- Python targeted baseline supplied at dispatch: 97 passed.
- Vitest targeted baseline supplied at dispatch: 46 passed.
- `npx tsc --noEmit` exits 2 only at `web/ts/eval/label-panel.ts:233` and `:242`: `placeholder` is not accepted by `Partial<HTMLElement>`.
- `git diff --quiet origin/main -- web/ts/eval/label-panel.ts` exits 0, proving that file and both errors are unchanged from `origin/main` at the Task 1 baseline.
- `label-panel.ts` is outside issue #223 Task 1 and must not be modified as part of these corrections.

## Task 1 correction evidence

- A223-07 red: `pytest tests/test_sfip.py -k no_vv -v` failed with structurally missing omega scoring 85.0 (`full_no_vv`) and 90.0 (`proxy_no_vv`) instead of 100.0. Green: both focused tests passed after normalizing only the present members; real `omega_pa_s=0.0` remained 85.0/90.0.
- A223-08 red: `test_cloud_overlap_merges_internal_overlaps_before_jaccard` returned 2.0. Follow-up red: empty vs zero-length canonical span returned 0.0, expected 1.0. Hardening red: NaN and infinite spans made empty-vs-invalid return 0.0 instead of 1.0. Green: all 10 DD/NWP overlap regressions passed after merging each side, rejecting non-finite and nonpositive spans, and intersecting the unions once.
- A223-06 red: the route-map test classified SFIP 15 as green. Green: `npx vitest run tests/unit/route-map-metrics.test.ts` passed 23 tests with 15/30/55 boundaries, matching mandated legend stops, and one-decimal tooltips at 29.9 and 54.9.
- A223-09 characterization: `test_gust_vector_crosswind_is_not_recalibrated_without_evidence` passed with AMBER from the current separate mean-crosswind/absolute-gust policy. The initial public guard passed despite contradictory hand-populated components and a stored 270° direction whose direct gust-crosswind formula was 0 kt, so that pass was inadequate. The corrected guard derives 12 kt crosswind / 5 kt headwind through `compute_runway_winds` from one consistent runway-090° wind, proves the stored direction makes a 28 kt gust exceed the 25 kt RED crosswind threshold, and still passes GREEN under the current raised absolute-gust limits; characterization only, not endorsement or recalibration.
- Final Task 1 target: 113 Python tests passed, 23 Vitest tests passed, and `git diff --check` exited 0.

## Intermediate fresh-model review evidence

An intermediate independent review found and resolved route-wide FIKI
triggering, CAT-without-omega, airport-axis completeness, wind/LLWS provenance,
and CAPE-fallback attribution defects in `59a76b51`, `397377d0`, `bf0a7853`,
`565ecf7a`, and `183f31ba`. Those corrections were necessary but were not the
final approval gate. In particular, the FIKI result was only route-wide-safe,
and the airport result only had the first availability/provenance layer; the
later clean-room pass below exposed narrower defects that those entries did not
cover.

The intermediate review also corrected two documentation contradictions in
`designs/analysis-metrics.md`: ECMWF `cp` is decoded and operational, and icing
descent escape uses the per-model `max(freezing level, cloud base)` before the
conservative cross-model `min()`.

## Final clean-room approval gate

- Scope: base `65f596ed2d45bbea34b849a5231150fe7dd9e9d9`, reviewed HEAD
  `6b67dc2dcb454387209d484e7c4f79436769e649`.
- Verdict: **APPROVED**. No blocking meteorological, missing-data, unit,
  attribution, threshold-display, or evidence-contract finding remains.
- Independence: the range is linear with no merge commits, and the review is
  standalone with no external pull-request dependency.

The final reviewer directly reproduced and closed six remaining blockers:

1. **Airport/IFR axes and browser missing-is-not-clear.** `aa470055` and
   `bb983fd` distinguish assessed-clear ceiling from missing ceiling/visibility,
   keep missing rows out of category voting while preserving independent wind,
   and make clear IFR results partial/unavailable when a required airport axis is
   absent. Regressions include
   `test_ifr_missing_airport_source_fields_is_partial_unavailable`,
   `distinguishes assessed clear from an unassessed null ceiling`,
   `excludes missing-derived VFR votes from the category summary`, and
   `renders an evidence-free condition with a muted category and valid wind`.
2. **Precipitation denominators.** `6b67dc2d` makes advisory percentages use
   only precipitation-assessed points while missing assessments remain partial.
   Regression:
   `test_partial_hazard_uses_only_precip_assessed_points_for_percentages`.
3. **FIKI local severity and affected union.** `02114469` keeps unrelated icing
   zones at their local grade and unions cruise concern with AMBER/RED terminal
   concern without double-counting. Regressions:
   `test_fiki_severe_transit_zone_does_not_raise_unrelated_zone_severity` and
   `test_fiki_cruise_and_terminal_concern_extent_is_unioned_without_double_counting`.
4. **Absent divergence semantics.** `6b67dc2d` treats
   `ModelDivergence.mean=None` as absent, not GOOD evidence; all-absent is
   unavailable, mixed valid/absent is partial, and a numeric mean remains valid
   with a null individual model value. Regressions:
   `test_model_agreement_all_absent_metrics_are_unavailable_not_good`,
   `test_model_agreement_mixed_valid_and_absent_metrics_is_partial`, and
   `test_model_agreement_numeric_mean_with_null_model_value_remains_complete`.
5. **Cross-model freezing-rain descent.** `02114469` makes an icing-bearing FZRA
   model block a finite descent escape offered by another model, while an
   ordinary no-icing model's `None` remains non-blocking. Regressions:
   `test_descend_freezing_rain_one_model_blocks_cross_model_escape` and
   `test_descend_model_without_icing_does_not_block_finite_escape`. An FZRA
   profile with no icing zones is explicitly outside this approved correction.
6. **Terminal-convection controlling provenance.** `aa470055` and `bb983fd`
   preserve terminal hazards without a top-level airport artifact and emit the
   controlling method or `flight_category_composite` for ties, including
   different methods whose HIGH/EXTREME risks both map to RED. Regressions:
   `test_terminal_hazard_survives_missing_airport_domain`,
   `test_tied_condition_and_convection_use_composite_provenance`, and
   `test_same_terminal_high_and_extreme_methods_tie_at_red`.

Final verification evidence:

- `venv/bin/pytest -q tests/analysis/advisories` — 665 passed.
- Reviewer-reported focused backend metric gate — 102 passed; the returned gate
  record did not retain the focused file-list command.
- `cd web && npx vitest run` — 36 files, 607 tests passed.
- Reviewer-reported focused frontend contract gate — 6 files, 117 tests passed;
  the returned gate record did not retain the six-file command.
- `git diff --check` — clean.
- `cd web && npx tsc --noEmit` — only the unchanged pre-existing errors at
  `web/ts/eval/label-panel.ts:233` and `:242`.
- No build or browser acceptance run was part of this meteorology gate.

Non-blocking evidence-dependent follow-ups remain #382 (Ri/SLD/resolution),
#383 (DD/okta labeling), #384 (route-graph above-scale ambiguity), and #385
(airport gust-vector calibration). The SFIP missing-omega change remains an
authorized missing-weight normalization: it removes a structurally absent
member and renormalizes present weights; it does not change membership functions
or severity thresholds and is not a recalibration.
