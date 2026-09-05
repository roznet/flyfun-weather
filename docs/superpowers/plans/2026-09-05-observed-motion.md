# Observed Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add opt-in experimental radar/cloud motion, route relationships and evidence inspection to both clients in existing draft PR #600.

**Architecture:** A shared server pipeline reads pinned local observations, tracks the two feature families independently and emits bounded versioned vectors. Pack publication and client persistence order success and failure using durable revisions. Web and native iOS render the same scientific result without computing their own motion.

**Tech Stack:** Python/Pydantic, NumPy/SciPy, pyproj/Shapely, existing HDF5/netCDF readers; TypeScript/Leaflet/Vitest/Playwright; Swift/SwiftUI/MapKit.

**Spec:** [Approved design](../specs/2026-09-05-observed-motion-design.md) and [normative record definitions](../specs/2026-09-05-observed-motion-contract.md). User approved implementation after reviewing these at commit `79b88b37`.

## Global Constraints

- `WB_OBSERVED_MOTION_ENABLED` off by default; also requires `WB_OBSERVED_ENABLED`. User prediction-mode opt-in defaults off.
- Version strings: `masked_contour_translation_v1`, `observed_motion_policy_v1`; wire key `observed_motion`, schema version 1.
- Both web and native iOS implement the vector explorer; Mac/iOS execution remains deferred and cannot be reported as passed.
- No provider requests in analysis; use local frames only. No shared `.env`, weather data, credentials, DB, dev server or `npm run build`.
- At most four primary frames, at least three distinct times; span 45 minutes; gaps DBZH 10 minutes / CTTH 20 minutes; reference freshness 20 minutes.
- 2 km AEQD grid; 262,144 cells, 1,024 maximum dimension, 1,000 km maximum centre distance; current 2 km OPERA spacing only.
- CTTH decode blocks at most 46 full-width rows and 262,144 source cells; radar windows at most 1,048,576 cells / 2,048 per dimension.
- Inspection contours: radar 5 dBZ and cloud 4,572 m / 15,000 geometric ft MSL. These are not storm thresholds.
- All remaining numerical tracking, geometry, serialization and scientific conditions in design §§3–8 and the contract are binding; do not weaken them to make fixtures pass.
- Projection ends at source reference +15 minutes, never request/device +15; at most three absolute future UTC five-minute ticks.
- Ground velocity, edge-to-leg closure and simultaneous planned overlap are distinct. No safe-route, thunderstorm diagnosis, vertical clearance, forecast skill or probability claims.
- CTTH ground movement/projection/quantitative association requires applicable reviewed real geolocation evidence. Synthetic tests cannot enable production.
- Explicit unavailable/disabled envelopes replace prior motion. Missing legacy data is not a refreshed result or negative weather evidence.
- Revision high-water survives same-public-identity pack deletion/recreation. Raw unknown JSON survives cache patches. Failures cannot be hidden by old ready data.
- Header `X-Observed-Motion-Enabled: 0|1`; lifecycle authority starts unknown, one cache-bypassing existing snapshot GET, 10-second deadline. No time/feature/source-selection polling.
- Declare SciPy >=1.10 and Shapely >=2.0, no pysteps/OpenCV/scikit-image/GDAL/rasterio. Only install in this worktree's `venv`.
- Preserve correction history. No force push, merge, deploy or message to Brice. Update existing draft PR only after implementation/testing/review; use the existing fork, not local-mirror `origin`.

## Execution and file ownership

Tasks 1–5, 9 and 10 are the server increment; task 6 is web; task 7 is native; task 8 verifies the integrated increment. Task 9 extracts the independently testable publication primitive from task 5 and can run alongside input work; task 5 later wires its reviewed helper into real writers/transports. Task 10 similarly extracts continuous route geometry/timing from task 4, so those pure calculations can be tested against the agreed grid/track interfaces before payload orchestration. Each has its own test/review gate. Tasks can be delegated on disjoint file sets when their consumed interfaces are stable; no concurrent edits or commits to shared files. The controller serializes commits and records ownership/review ranges in the plan-scoped ledger. This follows the environment's explicit parallel-delegation instruction while avoiding shared-file conflicts.

Python commands below use `env PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 WB_OBSERVED_LIVE_TESTS=0 venv/bin/python -m pytest -q -p no:cacheprovider`. API tests use their in-memory SQLite fixtures/temporary paths. Web commands run in `web/` with `PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin`.

### Task 1: Validated shared contract and policy

**Files:** Create `src/weatherbrief/models/observed_motion.py`, `src/weatherbrief/observed/motion/__init__.py`, `src/weatherbrief/observed/motion/policy.py`; tests `tests/observed/test_motion_contract.py`, `tests/observed/motion_fixtures.py`; modify `pyproject.toml` for direct dependencies. Do not wire snapshot/API yet.

**Interfaces:** Export every PascalCase record named in the normative contract. `ObservedMotion.model_validate(raw)` validates producers and preserves extra fields. Export `empty_motion(*, route_geometry_id: str, planned_timing_id: str | None, cutoff_at: datetime, revision: int, status: str, reason_codes: list[str]) -> ObservedMotion` and `MotionPolicy`/`DEFAULT_POLICY` for the exact approved bounds. Empty builder populates all completeness categories without manufacturing evaluated zeros.

- [ ] Write contract tests using literal unavailable records and valid finite polygons. Observe failure before adding the module. Representative rejection:

```python
def test_failure_envelope_cannot_carry_an_accepted_velocity():
    raw = unavailable_motion_dict()  # test utility constructs literal contract fields
    raw["features"] = [accepted_radar_feature_dict()]
    with pytest.raises(ValidationError):
        ObservedMotion.model_validate(raw)
```

- [ ] Implement explicit Pydantic record models with `ConfigDict(extra="allow", allow_inf_nan=False)`, aware-UTC normalization, required nullable fields, safe strict integer revisions, geometry bounds/topology and cross-record identity/state validation. Do not use `dict[str, Any]` as a substitute for a listed nested record. Validation must distinguish unavailable geometry, zero velocity, empty evaluated intervals and not-evaluated results.

```python
def test_newer_failure_roundtrips_unknown_fields():
    raw = unavailable_motion_dict(revision=9)
    raw["future_extension"] = {"source_with_underscores": 42}
    result = ObservedMotion.model_validate(raw).model_dump(mode="json")
    assert result["revision"] == 9
    assert result["future_extension"] == {"source_with_underscores": 42}
```

- [ ] Add tests for unsafe/bool revisions, naive/nonfinite dates/numbers, dangling references, disabled contents, unsupported root states, accepted cloud without registration, lightning marker/evidence count invariants and payload limits. Add policy only as used production constraints, not tests that merely assert constant text.
- [ ] Run focused tests red then green, install declared wheels in `venv`, run `pip check`, self-review; ask controller to commit only owned files as `feat: define validated observed-motion contract` and dispatch task review.

### Task 2: Cutoff-safe inputs and bounded common geometry

**Files:** Create `observed/motion/history.py`, `geometry.py`, `validation.py`; modify `observed/frames.py`, `lightning.py` and source metadata readers only where required to expose real acquisition semantics/precision; tests `tests/observed/test_motion_history.py`, `test_motion_geometry.py`, existing frame/lightning tests. Paths are under `src/weatherbrief/` unless prefixed `tests/`.

**Interfaces:** `AnalysisGrid` contains `crs: str`, `center: tuple[float,float]` (lon,lat), `origin_x_m`, `origin_y_m`, `width`, `height`, `cell_size_m`; inverse/project methods and `to_record() -> AnalysisDomain`. `AnalysisFrame` contains `source_id`, `frame_id`, `reference_at`, `grid`, `descriptor: ndarray`, `known: ndarray[bool]`, `detected: ndarray[bool]`, `values: ndarray`, `temperature_k: ndarray | None`, `source_record: FrameRecord`, `geolocation: GeolocationRecord`. Rows in analysis arrays increase northward. `select_history(store, source_id, cutoff_at, policy=DEFAULT_POLICY)` returns pinned stored-frame records plus reasons. `load_history(store, route, cutoff_at, policy=DEFAULT_POLICY, *, deadline=None)` returns a result with `.grid`, `.frames_by_source`, `.sources`, `.reason_codes` and bounded RATE/LI context. `geometry.footprint(mask, grid)` returns unsimplified Shapely cell unions; `geometry.display_geometry(shape, grid)` returns `GeometryRecord`.

- [ ] Add synthetic retained-file tests that reject a future receipt/acquisition even when the filename rounds below cutoff, stop at corrupt middle frames and report permitted missing-publication gaps.

```python
def test_asof_does_not_select_future_receipt(tmp_path):
    store = make_three_frame_store(tmp_path, last_received="2026-09-05T12:01:00Z")
    selected = select_history(store, "opera_dbzh", utc("2026-09-05T12:00:00Z"))
    assert all(f.received_at <= utc("2026-09-05T12:00:00Z") for f in selected.frames)
    assert "insufficient_history" in selected.reason_codes
```

- [ ] Extend the shared frame-store as-of primitive, pin content/grid/product/receipt/window identity and recheck after reads. Retain existing latest/observation semantics. Use documented acquisition metadata; if it is unavailable, return `missing_acquisition` for motion, never invent a precise time.
- [ ] Implement route-bounded AEQD grid construction/padding/caps; nearest radar sampling with three-state masks; streamed CTTH corrected quadrilateral sampling and highest-top/own-temperature winner. Use binary high-top descriptor with known lower/clear as background and unknown masked. Synthetic quadrilateral/holes/sign tests precede each step.

```python
def test_unknown_hole_is_not_filled():
    shape = footprint(np.array([[1,1,1],[1,0,1],[1,1,1]], dtype=bool), grid_3x3())
    assert shape.area == 32_000_000
    assert not shape.covers(Point(3000,3000))
```

- [ ] Extend `FlashFrame`/reader with explicit individual-time/window-only provenance; mismatched/masked/invalid/out-of-window timestamps remain regional context, no fake precision. Registration factory applies real evidence manifests separately; no production synthetic approval.
- [ ] Run focused/adjacent observed-reader tests and review resource-limit/corner fixtures; controller commits owned files and requests review before tracking consumes the interfaces.

### Task 3: Independent conservative feature tracking

**Files:** Create `observed/motion/tracking.py`; tests `tests/observed/test_motion_tracking.py`. Consume task-2 geometry, not source reader internals.

**Interfaces:** `TrackSample(frame_id, reference_at, footprint)` and `Track(feature_id, source_id, reference_at, footprint, history, velocity_xy_m_s, reason_codes, pair_diagnostics, fit_rms_residual_cells)` dataclasses. `track_history(frames: Sequence[AnalysisFrame], *, route_geometry: BaseGeometry, policy=DEFAULT_POLICY, deadline=None) -> TrackingResult`. The result has `.tracks: list[Track]`, `.reason_codes`, and explicit `.counts` for full-field detections, small detections, eligible candidates, selected candidates and emitted observed features; unevaluated counts are null with a completeness flag, never inferred zero from an empty list. Exact count scopes are documented and consumed by payload completeness. The supplied projected continuous route is used to rank candidates, not inferred from the grid centre. Footprints are unsimplified metric Shapely geometry; velocity is `tuple[float,float] | None`, never a fabricated zero. `history` contains observed `TrackSample` records.

- [ ] Write literal translations and observed-only failures before implementation. Synthetic zero and unknown are separate:

```python
def test_clean_stationary_echo_is_not_unknown():
    track = tracked_square_history(offsets=[(0,0),(0,0),(0,0)])[0]
    assert track.velocity_xy_m_s == pytest.approx((0,0))
def test_failed_latest_match_cannot_reuse_old_velocity():
    tracks = track_history(history_with_split_latest(), route_geometry=fixture_route_line()).tracks
    assert all(t.velocity_xy_m_s is None for t in tracks)
```

- [ ] Label full bounded fields before candidate selection; two usable variance-ranked patches per pair; fixed support across searched shifts; masked NCC, peak margin/boundary checks, deterministic quadratic fallback, arithmetic mean, reverse consistency. Require whole-feature support/rim and full relevant lineage including small/unselected competitors.
- [ ] Use a clean newest-ending chain, previous-pair next-observation residual and elapsed-time least-squares of cumulative displacements. Preserve scalar/appearance diagnostics rather than fitting changing centroids. Return observed-only tracks with reasons on low texture, ambiguity, clipping, split/merge, size or deadline.
- [ ] Tests cover opposing radar/cloud vectors, cloud-only, 4-frame failed newest, fractional shifts, support clipping with clean interior, capped small competitor, speed limits and budget exits. Run focused tests; controller commits and reviews this task.

### Task 4: Route relationships, associations and bounded payload

**Files:** Consume the reviewed `observed/motion/route.py` and strict route-walker extension from task 10. Create `observed/motion/association.py`, `payload.py`. Tests `tests/observed/test_motion_association.py`, `test_motion_payload.py`; task 10 owns route/route-walker tests.

**Interfaces:** `build_observed_motion(route: RouteConfig, *, departure_time: datetime | None, cutoff_at: datetime, revision: int, store: FrameStore | None = None) -> ObservedMotion`. `route_identities(route, departure_time) -> tuple[str,str|None]`. `build_route_geometry(route, grid) -> BaseGeometry` supplies a projected continuous route to tracking's candidate ranking. `route_relationships(track, route, grid, departure_time, cutoff_at, projection_times) -> tuple[list[RouteRow], PlannedOverlapResult]`. `associate_tracks(tracks, frames_by_source, grid, context) -> tuple[list[AssociationRecord], list[LightningRecord], dict[str,FeatureLightningEvidence]]`; this internal map never appears as ID-keyed JSON. Grid is the task-2 type and tracks the task-3 type.

- [ ] Integrate task 10's independently tested route relationships and planned-overlap calculations. Its tests cover a moving polygon crossing between UI ticks, a route bend, holes/tangent/zero relative movement and already-intersecting closure.

```python
def test_ground_speed_is_not_route_closure():
    rows, overlap = route_relationships(parallel_motion_track(), north_south_route(),
        metric_grid(), departure_time=None, cutoff_at=NOON, projection_times=[])
    assert rows[0].relationship == "approximately_unchanged"
    assert rows[0].closure_kt == pytest.approx(0, abs=0.01)
    assert overlap.status == "unavailable"
```

- [ ] Use the reviewed route helpers for dense great-circle legs, closure and continuous planned overlap; use the same source-bounded intervals and planned distance-fraction timing in payloads. Convert representative speed via the reviewed inverse AEQD + WGS84 one-second displacement helper.
- [ ] Test compatible asynchronous overlap vs unavailable time/registration; rain RATE remains independent; lightning precision remains observed and feature summaries precede marker caps. Translate only within bracketed observed histories for association; keep identities/vectors separate.
- [ ] Orchestrate bounded independent source tracks, source statuses, per-feature observations, UTC times and projection geometries. Enforce caps/deterministic omission counts and 1 MiB serialized UTF-8; preserve feature cards/positive evidence, never silently simplify beyond tolerance or return a negative on incomplete evaluation. One cooperative computation/process, 15-second budget. Gate defaults off; runtime refusal returns an explicit envelope.
- [ ] Test no network access, independent unavailable source, CTTH production gate, >256 lightning markers with retained positive summary, 48-feature/geometry/interval caps, expired lead, empty history, busy/error replacement. Run relevant tests; controller commits/reviews.

### Task 5: Revision-fenced publication and every server transport

**Files:** Consume reviewed `storage/observed_motion.py` and publication tests from task 9; modify `models/analysis.py`, `models/observations.py`, `models/__init__.py` as needed, `pipeline.py`, `tasks/artifacts.py`, `tasks/route_weather.py`, `storage/snapshots.py`, `storage/flights.py`, `tasks/retention.py`, `api/app.py` pack deletion paths and `api/packs.py`. Tests `tests/test_api_observed_motion.py` and existing pack/realtime/retention tests.

**Interfaces:** `reserve_motion_revision(pack_dir: Path, *, allow_create=False) -> MotionPublicationToken`; token contains public identity/generation/revision. `publish_motion_snapshot(pack_dir, token, motion, *, refreshed_fields, initial_snapshot=None) -> dict`; merges under stable parent lock, validates generation/latest attempt, preserves unrelated JSON and returns current published snapshot for superseded callers. Shared full/legacy writers must pass through the same atomic helper. Deletion invalidates generation while preserving high-water. Helpers return explicit transport errors on invalid lifecycle, never recreate deleted packs. `observed_motion` is the same optional raw-compatible sibling in snapshot/full/direct/gated/SSE response models.

- [ ] Use real temporary files/concurrent barriers for newer-unavailable/older-ready publication, initial creation, reused-pack writer and deletion/recreation races.

```python
def test_newer_failure_fences_old_success(pack_dir):
    old = reserve_motion_revision(pack_dir)
    new = reserve_motion_revision(pack_dir)
    publish_motion_snapshot(pack_dir, new, unavailable(new.revision), refreshed_fields={})
    result = publish_motion_snapshot(pack_dir, old, ready(old.revision), refreshed_fields={})
    assert result["observed_motion"]["revision"] == new.revision
    assert result["observed_motion"]["status"] == "unavailable"
```

- [ ] Wire task 9's reviewed stable lock/control and atomic helpers into every direct snapshot writer/deletion. Full creation is explicitly distinct from refresh; no bypass restores an older motion block.
- [ ] Compute from the same snapshot route/timing identity captured at reservation, using task 4's shared `route_identities`. Do not replace those inputs with a later flight-DB route. Verify initial complete snapshot inputs before first publication; storage freezes raw identity fields but does not derive a competing hash algorithm.
- [ ] Include retention full-pack deletion and account deletion's individual-pack loop in the same generation lock. Keep the generic parent-directory cleanup distinct from the pack-only helper: account deletion also passes the whole user packs directory to `_rmtree`. Reusable public pack identities must retain high-water control/lock state; a fully removed account namespace cannot be reused after that state is lost. Test delayed publication and preservation on ordinary pack/retention deletion.
- [ ] Call task-4 payload during full/realtime optional stage with captured cutoff. Completed failures/disabled/outside-D-0 publish replacements; transport failure does not claim computation. Preserve ordinary observations.
- [ ] Add serve-time capability/no-store headers for existing snapshot/bundle/refresh paths; propagate full motion through all return models, including SSE complete and direct observations refresh. Tests exercise actual endpoints and pack bundles, not mock existence.
- [ ] Run targeted API/storage suites, inspect all changed-writer callers; controller commits/reviews before clients depend on endpoints.

### Task 6: Web explorer, tolerant ordering and lifecycle

**Files:** Create `web/ts/observed-motion/types.ts`, `state.ts`, `view.ts`, `map-layer.ts`; modify `web/ts/store/types.ts`, `briefing-store.ts`, `adapters/api-adapter.ts`, `briefing-main.ts`, relevant route-map ownership entrypoint and `web/css/briefing.css`. Tests `web/tests/unit/observed-motion.test.ts`, `observed-motion-state.test.ts`, `web/tests/observed-motion-browser.spec.ts`, extend the existing no-server observed browser configuration/fixtures.

**Interfaces:** `parseObservedMotion(raw: unknown)` returns a tolerant validated view or unavailable reason while retaining raw JSON; `MotionState` owns revision/identity/request-generation/capability/time selection. `ObservedMotionView` owns accessible controls/cards/table; `ObservedMotionMapLayer` owns an independent Leaflet group with `setData`, `selectTime`, `selectFeature`, `clear`, `destroy`. Network adapter returns capability separately from stored motion and exposes a cache-bypassing existing-snapshot read; zero client scientific calculations.

- [ ] Test a newer unavailable response replacing old ready; unknown raw keys, unsupported schema, missing legacy data, foreground expiry and generation mismatch. Write real UI interactions for two families and hole geometry.

```typescript
it('does not resurrect ready data after a failed newer run', () => {
  const state = new MotionState();
  state.accept(readyFixture(8));
  state.accept(unavailableFixture(9));
  state.accept(readyFixture(8));
  expect(state.current?.status).toBe('unavailable');
});
```

- [ ] Add opt-in Experimental motion mode, independent source outlines/trails, server UTC projection controls, source-timed cards/association selection and selected-feature route/time table. Keep ordinary raster preference; no raster underlay in motion mode. Display all reasons/contour definitions/experimental limitations and feature lightning summary independently of markers.
- [ ] Implement nonpersisted capability unknown on lifecycle entry; coalesced cache-bypassing existing-snapshot GET with 10-second deadline. Failed/missing authority permits only stored analysis. Revision merge handles disk-equivalent browser snapshot reload and all refresh paths; time changes never fetch/recompute.
- [ ] Real-entrypoint browser tests exercise mode entry, capability revocation, selection/focus, holes, independent vectors, refresh failure, time expiry/date display, narrow layout and layer teardown. Run Vitest + application TypeScript + Chrome harness without `npm run build`; controller commits/reviews.

### Task 7: Native iOS explorer and raw cache durability

**Files:** Under `app/flyfun-weather/flyfun-weather`, create `Models/ObservedMotion.swift`, `ViewModels/ObservedMotionState.swift`, `Views/Map/ObservedMotionView.swift`, `Views/Map/ObservedMotionOverlay.swift`; modify snapshot/refresh models, `BriefingViewModel`, `RouteMapView`, `RouteMapKitView`, repository/network/cache services and `AppIntents/RefreshDriver`. Add `flyfun-weatherTests/ObservedMotionTests.swift`, `ObservedMotionCacheTests.swift`, relevant existing cache tests and `flyfun-weatherUITests` scenario; register new files if project format requires.

**Interfaces:** Tolerant raw-motion value preserves JSON spelling/unknown fields from original bytes, with typed validated view access. Snapshot/refresh `observedMotion` optional sibling does not fail the whole briefing on malformed data. `ObservedMotionState` mirrors web identity/revision/capability/expiry rules; MapKit weather ownership is independent of route overlays. Shared cache actor merges raw motion on both patches and full snapshot/bundle writes.

- [ ] Write Swift fixture tests before production changes: raw unknown key round trip, newer-unavailable/older-bundle ordering, same-revision conflict, missing/unsupported block, deleted pack and save failure. These tests are authored/statically reviewed here, not reported as executed.

```swift
func testUnknownCapabilityDoesNotAuthorizeCachedPrediction() {
    let state = ObservedMotionState()
    state.accept(raw: readyFixture(revision: 8))
    XCTAssertFalse(state.canPresentActivePrediction)
}
```

- [ ] Decode raw motion from original JSON bytes or an explicitly raw-preserving boundary; never rebuild cache JSON from lossy typed DTOs or mutate identifier keys through global snake-case conversion. Make patch/full-download writes revision-aware, preserve root keys, byte accounting/protection, initiating pack binding and errors in Siri/in-app callers.
- [ ] Implement the same source/time controls/cards/relationships/evidence as web, MapKit polygons with holes, observed vs projected line styles and selected association highlighting. Existing route recolor/aircraft updates must retain weather overlays. Cards work without basemap tiles.
- [ ] Wire the fresh capability snapshot read on lifecycle entry/foreground/reconnect with cancellation/generation/deadline and stored-only fallback. No clock/time-toggle fetches. Add tests for source expiry, invalid clock, UTC dates, navigation callbacks and overlay ownership; static compile/API review here, Mac compile/unit/UI deferred.
- [ ] Controller commits native files and dispatches independent Swift contract/lifecycle review; do not claim DTO-only work as native completion.

### Task 8: Integrated verification, findings and PR update

**Files:** Extend test fixtures/integration tests only for actual uncovered behavior; update `designs/current-conditions.md`, relevant API/native design docs, `designs/reviews/2026-09-05-observed-request-coverage.md`, create `designs/reviews/2026-09-05-observed-motion-verification.md`; update implementation checklist and spec status accurately.

**Interfaces:** All task contracts above meet at real snapshot/refresh/bundle/UI boundaries. Record exactly which feature families produce experimental results and which are source-gated.

- [ ] Execute shared serialized fixtures through Python/TypeScript boundaries, inspect authored Swift fixture parity; add a regression before changing code for any newly found integration defect.
- [ ] Run full isolated Python suite, web unit suite, application typecheck and observed Chrome harness, capturing commands/counts/skips/errors. Run focused failure reproductions rather than claiming science from synthetic tests.
- [ ] Independent whole-increment review against approved requirements (implementation base `79b88b37`, retaining published corrections); fix findings with covering tests and scoped re-review. Record unresolved real-source validation and unexecuted Mac checks.
- [ ] Update Markdown requirement status per delivered component. No real granule/replay claim without actual authorized evidence; no synthetic manifest enabled in production. Keep feature gated off.
- [ ] Prepare commit(s), verify clean worktree and diff, then update existing fork branch/PR #600 using explicit `https://github.com/downle/flyfun-weather.git`, preserving draft. Read resulting head/checks/comments and report actual outcome. Do not merge/deploy or send Brice a message.

### Task 9: Independently testable publication primitive (before task 5)

**Files:** Create `src/weatherbrief/storage/observed_motion.py` and `tests/test_observed_motion_publication.py`. Own only these files. No pipeline/API/model changes.

**Interfaces:** `MotionPublicationToken` frozen dataclass binds resolved pack path, generation, revision. `MotionPublicationError` represents lifecycle/transport failures. Implement `reserve_motion_revision(pack_dir: Path, *, allow_create=False) -> MotionPublicationToken` and `publish_motion_snapshot(pack_dir: Path, token: MotionPublicationToken, motion: ObservedMotion, *, refreshed_fields: dict, initial_snapshot: dict | None = None) -> dict`. Also export `write_snapshot_atomic(pack_dir: Path, snapshot: dict) -> dict` for legacy/full writes that must preserve the current motion block and unknown existing fields, and `delete_motion_pack(pack_dir: Path) -> None` for generation-fenced deletion; integration follows in task 5.

- [ ] Write real temporary-file tests before implementation: an existing `briefing.json` gets two reserved revisions, newer unavailable publishes first and old success cannot replace it; unrelated unknown root JSON remains. Same-revision different content is a contract error. Initial full-writer creation is allowed explicitly but ordinary refresh cannot create or resurrect a pack.

```python
def test_deletion_preserves_high_water_and_fences_old_generation(tmp_path):
    pack = tmp_path / "pack"
    first = reserve_motion_revision(pack, allow_create=True)
    delete_motion_pack(pack)
    second = reserve_motion_revision(pack, allow_create=True)
    assert second.revision > first.revision
    with pytest.raises(MotionPublicationError):
        publish_motion_snapshot(pack, first, None, refreshed_fields={}, initial_snapshot={})
```

- [ ] Implement a parent-scoped stable lock file using `fcntl.flock`, separate atomically replaced control record, persisted high-water and invalidatable generation. Reserve under lock, compute outside (caller-owned), recheck generation/latest attempt/current snapshot under lock and atomic replace intended fields. Initial creation records pending full-writer ownership; only matching current token can publish a complete initial snapshot. Refuse unreadable/corrupt control/snapshot rather than resetting the counter.
- [ ] Atomic writer rereads current JSON, preserves unknown fields and newer/equal-identical motion, refuses same-revision conflicts and identity contradictions. Superseded publication returns current snapshot; no current snapshot means explicit error. Current failure with higher revision replaces geometry rather than skipping nil. Use validated `ObservedMotion` from task 1 when present; lifecycle rejection occurs before inspecting superseded/invalid body data.
- [ ] Tests cover separate stable lock inode, concurrent reserve/publication via barriers, snapshot readers never observing partial JSON, safe-integer exhaustion, retained high-water, wrong-path token, deleted/recreated path, old full-writer content and failed atomic write preserving prior JSON. All deletion fixtures live under pytest tmp_path; no shared data.
- [ ] Run focused tests red then green; controller commits owned files and dispatches a dedicated concurrency/quality review. Task 5 cannot bypass these helpers after review.

### Task 10: Continuous route/timing primitive (before task 4)

**Files:** Create `src/weatherbrief/observed/motion/route.py`; extend `src/weatherbrief/fetch/route_walk.py` with an opt-in strict segment limit without changing defaults. Tests `tests/observed/test_motion_route.py` and relevant route-walker tests. Own only these files.

**Interfaces:** `route_identities(route, departure_time) -> tuple[str,str|None]`; `build_route_geometry(route, grid) -> BaseGeometry`; `route_relationships(track, route, grid, departure_time, cutoff_at, projection_times) -> tuple[list[RouteRow], PlannedOverlapResult]`; `ground_velocity(track, grid) -> tuple[float, float|None, tuple[float,float]]` gives speed knots, bearing degrees true/null, and reference point lon/lat. `track` consumes task 3's agreed data fields; `grid` consumes task 2's `AnalysisGrid`. The pure route functions must not access providers/storage or construct alternative motion models. Task 4 imports these reviewed primitives.

- [ ] Write literal/hand-calculated geometry tests first: movement parallel to a leg has nonzero ground speed but unchanged closure; intersection has distance zero and closure not applicable; a moving contour crossing between UI ticks yields a continuous planned interval. Include holes, multiple intervals, tangencies and zero relative displacement.
- [ ] Reuse the great-circle distance convention and preserve every original waypoint/bend/leg index (including repeated labels). Strict densification is opt-in, at most 1 NM per segment and 2,048 segments; defaults and unrelated advisory behavior remain unchanged. Degenerate legs do not erase valid legs or gain passage intervals.
- [ ] Implement route geometry and a single deterministic geometry/timing identity algorithm. Invalid timing yields null timing ID, not a guessed speed/departure. Planned timing uses aware departure plus duration times cumulative-distance fraction only. Reject nonfinite/invalid route or durations and unsupported segment counts explicitly.
- [ ] Compute minimum full-contour/continuous-leg distance; centered 60-second supported finite-difference closure, shortened at reference/expiry boundaries. Positive means distance decreasing; magnitude below 1 kt is approximately unchanged. Each leg/time retains its own identity and reasons.
- [ ] Solve continuous overlap using relative aircraft segments versus the unsimplified translated contour. Preserve holes/tangent instants/multiple intervals; zero relative displacement uses point coverage. Restrict to the planned/source-supported interval and whole-contour domain support; fail unavailable (not evaluated-empty) on geometry/interval/resource limits. Round output outward to minutes, clamp to evaluated interval, and distinguish tangent instants.
- [ ] Convert the latest contour centroid and its one-second grid translation through inverse AEQD then WGS84 geodesic; return true toward bearing and knots, zero speed with null bearing. Test off-centre projection orientation, not raw grid-axis atan2.
- [ ] Test no future lead, invalid timing, before-arrival crossing, repeated/zero legs, route cap, interval cap and fractional-domain exits. Run focused route/adjacent walker tests red then green; controller commits owned files and dispatches an independent numerical/spec review.

## Coverage self-review

Design §§1–3: tasks 1/4/6/7/8; §§4–5: tasks 2/3; §§6–7: tasks 4/10; §8 and contract: tasks 1/4/6/7; §9: tasks 5/6/7/9; §10: tasks 6/7; §§11–12: task 8 and each task's red/green/review gate. Both clients, independent cloud, lightning precision/summary, continuous planned overlap, server/client cache ordering and every transport have explicit owners.

Source registration and forecast usefulness are evidence gates, not work silently replaced by unit tests. Public/authorized real-source acquisition needs its own documented step; this plan neither reads shared data nor enables production to bypass it.
