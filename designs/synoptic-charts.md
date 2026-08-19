# Synoptic Hewson overlay on chart basemaps (DWD + Met Office)

> **Status: SHIPPED and on main** (Phases 0–5). Promoted out of `designs/future/`
> (2026-08-17) because this is now the design record for live code, not a
> proposal. Two items were deliberately deferred, not forgotten: URL state for
> `syn.base`, and gap-based disabling of the basemap picker. Read the phase list
> below as a build record.

Supersedes GitHub issue #164 and folds in the Met Office chart source (merged
into main after #164 was written). #164's architecture stands; this doc records
the deltas and the implementation of the **two-source** version.

## Goal

In the maps page's **Synoptic Forecast** tab, add a basemap picker so the
Hewson gridded overlay (θe / |∇θe| / -∇²θe / TFP / advection / tendency) **and
the gate-detected front polylines** render on top of a surface-analysis / front
chart instead of OSM/CARTO tiles. The chart can come from **DWD** or **Met
Office**, picked whose valid time is closest to the user-selected Hewson valid
time. Grid cells and front lines are re-projected into the chart's
polar-stereographic pixel space so they align with the chart's isobars/fronts.

Route overlay is out of scope (maps page is flight-independent), same as #164.

## State of the world (2026-06)

| Fact | Detail |
|---|---|
| Met Office charts merged | `fetch/metoffice_charts.py`, `tasks/metoffice_charts.py`, API endpoints in `packs.py` — all in main. |
| Met Office **is calibrated** | `metoffice_charts.py` `_CHART_CALIBRATIONS["colour"]` has a real homography (calibrated 2026-05-29 from 8 graticule crossings, max err 1.33 px, rms 0.58 px). `is_calibrated("colour")` → True. |
| Identical projection recipe | Both sources: pyproj polar-stereo + 8-coeff 2D homography. `lonlat_to_chart_pixel` is duplicated near-verbatim in both modules. |
| Three calibration variants | DWD `analysis` (4389×3114, lat_ts=90, lon_0=10); DWD `icon` (800×653, lat_ts=60, lon_0=5); Met Office `colour` (800×540, lat_ts=60, lon_0=0). |
| Front polylines are cheap | `synoptic-map.ts` `setFronts()` draws `L.polyline` from lat/lon. In `L.CRS.Simple` they reproject **natively** once fed chart-pixel coords as latLng — no canvas math. The grid cells are the hard part. |
| Chart-id / offset sets differ | DWD: `ana,036,048,060,084,108`. Met Office: `ana,012,024,036,048,060,072,096,120` (finer + longer → **better time-match basemap**). |
| Extensive Python duplication | `select_default_chart_id`, `cache_root`, `cycle_dir`, `list_cycles`, meta read/write, `_atomic_write_bytes`, `resolve_chart_path`, `chart_meta`, `evict_old_cycles`, `lonlat_to_chart_pixel`, `build_route_overlay`, `ChartFetchResult`, `RefreshReport`, `_fetch_one` — all mirrored. Source-specific only: cycle discovery (Last-Modified vs JSON index), chart-id set, calibrations, native sizes, file extension, keep-count. |

## Enablement model ("if enabled for a user")

There is **no per-user "prefer DWD vs Met Office" preference**. Layered gating:

- **Synoptic tab visible** → user opted into the experimental front-detection
  optional service (existing gate, unchanged).
- **DWD basemap** → available to anyone who can see the tab.
- **Met Office basemap** → additionally requires `admin` OR
  `METOFFICE_CHARTS_PUBLIC=1`. As built the gate lives in
  `api/_chart_serving.py` (`metoffice_charts_allowed()` →
  `metoffice_charts.public_enabled()` or `require_admin`, wrapped by
  `source_allowed(spec, …)` keyed off `ChartSourceSpec.admin_gated`);
  `packs.py` keeps `_metoffice_charts_allowed` as a delegating shim.

Implementation: the manifest endpoint **only lists sources the caller is
allowed to see**. The picker shows OSM + DWD always; Met Office appears only
when permitted. No new pref column.

## Decisions (locked)

1. **Unified, source-parameterized endpoints** — one router, `{source}` path
   param, single place for the per-user gate.
2. **Extract a shared chart base now** — factor the duplicated projection +
   cache + selection logic into a shared chart-cache type both modules reuse.
   Keep existing module-level function names as thin shims so callers
   (`packs.py`, `pipeline.py`, `scheduler.py`, `tasks/*`, `frontal/cli.py`,
   `storage/*`) don't break. (As built: a single composable `ChartCache`
   class each module *instantiates* with declarative config + a
   `chart_type_for` callback — composition, not subclassing.)
3. **Both DWD + Met Office together** — single 3-way picker; the projection
   generalization is the same work either way.

## Plan

Sequenced so the riskiest pieces (shared-base refactor; projection
equivalence) land and get verified before the UI depends on them.

### Phase 0 — Shared chart base (Python)

**New**: `src/weatherbrief/fetch/chart_cache.py`

As built (the base is a *composable* `ChartCache`, not an abstract subclassed
`ChartSource`):

- `ChartCalibration` dataclass: `proj: dict`, `homography: tuple[8] | None`,
  `native_size: (w, h)`.
- `ChartCache.project(lon, lat, chart_type) -> (int, int)` — the shared pyproj +
  homography math (single copy). (`is_calibrated(chart_type)` gates it.)
- `ChartCache(...)` constructor takes declarative config: `slug`
  (`"dwd"`/`"metoffice"`), `display_name`, `subdir`, `extension`
  (`png`/`gif`), `chart_ids`, `forecast_offsets_h`, `calibrations:
  Mapping[chart_type, ChartCalibration]`, `chart_type_for` (callback),
  `keep_cycles`, `user_agent`, `timeout`.
- shared methods on the class: `cache_root`, `cycle_dir`, `list_cycles`,
  `read_meta`, `write_meta`, `atomic_write_bytes`, `resolve_chart_path`,
  `chart_meta`, `evict_old_cycles`, `evict_cycles_older_than`,
  `select_default_chart_id`, `build_route_overlay`, `project`, `fetch_one`,
  `make_session`, `conditional_headers`, `apply_results_to_meta`.
- Source-specific cycle discovery (DWD = Last-Modified probe; Met Office = JSON
  index) lives in each module, not on `ChartCache`.
- `dwd_charts.py` + `metoffice_charts.py` each build a module-level `_cache =
  ChartCache(...)` and keep their current module-level functions as thin
  delegating shims (back-compat).

**Done when**: existing DWD + Met Office tests pass unchanged; both refresh
tasks still produce identical cache layouts.

**Risk**: touches working briefing-chart code. Mitigation: stable public
signatures (shims) + full chart test suite + a refresh smoke run for both.

### Phase 1 — TS projection port + equivalence test

- **New** `scripts/dump_chart_calibrations.py` — imports the calibrations from
  **both** modules, writes:
  - `web/ts/visualization/chart-projection-constants.ts` — 3 keyed entries:
    `dwd-analysis`, `dwd-icon`, `metoffice-colour`.
  - `web/tests/unit/fixtures/chart-projection.json` — ~25 ref (lat,lon)→pixel
    pairs per chart type (corners + dense Hewson grid + edge points).
- **New** `web/ts/visualization/chart-projection.ts` — Snyder polar-stereo
  forward + inverse + 8-coeff homography forward/inverse;
  `makeChartProjection(key)` / `makeChartInverseProjection(key)`.
- **New** `web/tests/unit/chart-projection.test.ts` — forward within 1 px,
  round-trip < 1 px, for all 3 keys.

**Why early**: a global px drift slides the grid off the isobars; far cheaper to
catch standalone than after the renderer exists.

### Phase 2 — Unified backend endpoints

**New**: `src/weatherbrief/api/synoptic_charts.py`

- `GET /api/synoptic-charts/manifest` → `{ sources: [ { slug, label,
  attribution_html, run_cycle, issued_at, charts: [ { id, offset_h, chart_type,
  native_size, valid_time } ] } ] }` (built by `build_source_manifest`). DWD
  always included; Met Office only if `source_allowed`. A source with no cached
  cycle is omitted entirely. Latest cycle per source via `list_cycles`.
- `GET /api/synoptic-charts/{source}/{run_cycle}/{chart_id}` (as built: no
  `.{ext}` suffix on the route) → shared serve helper (`serve_chart_bytes`,
  which lives in `api/_chart_serving.py`, not in `synoptic_charts.py`):
  validate `source` slug, `run_cycle`, `chart_id` allowlist; long immutable
  `Cache-Control`; gate Met Office (404 not 403 so non-admins can't probe).
  Status codes as built: 404 unknown/gated source, 400 bad chart id or
  run-cycle, **410** when the bytes are evicted or the run skipped that offset.
- Refactor the existing flight-scoped chart endpoints in `packs.py` to call the
  same shared serve helper (behavior unchanged). Both `synoptic_charts.py` and
  `packs.py` import `serve_chart_bytes` + `SOURCES` from `_chart_serving.py`.

### Phase 3 — HewsonGridLayer pluggable projector

`web/ts/visualization/hewson-grid-layer.ts`: as built a
`setProjector(p: ChartProjector | null)` method (not a constructor option).
`null` = current Web-Mercator path, untouched. When set: per-cell 4-corner quad fill in chart-pixel space
(via `L.CRS.Simple` latLng swap `L.latLng(py, px)`), viewport cull on projected
center. Perf note from #164: in `CRS.Simple` Leaflet may translate the pane on
pan, so the canvas only redraws on zoom/viewreset/resize/setSlice — quad-fill
cost likely near-free.

### Phase 4 — SynopticMap chart-basemap mode

`web/ts/visualization/synoptic-map.ts`: `setBasemap(mode: 'osm'|'chart',
spec?)`. OSM↔chart transitions preserve geographic bbox (project/inverse-project
`getBounds()`, `fitBounds`). Chart mode = `L.CRS.Simple` + `L.imageOverlay` at
native dims + grid layer with projector. **Front polylines**: project each
coord and feed as `L.latLng(py, px)` — native reprojection (the priority item,
nearly free). Hover tooltip uses `makeChartInverseProjection` to recover lon/lat.

### Phase 5 — maps-main wiring

`web/ts/maps-main.ts`: basemap picker rebuilt from the manifest — a fixed `Map`
(OSM) button plus one button per allowed source using its manifest `label`
(`repopulateBasemapPicker`), so it is 2- or 3-way depending on the gate rather
than a hardcoded 3-way. Manifest fetched lazily on first chart toggle and
cached for the session; if the active source vanishes (cache evicted) the
picker falls back to OSM. Per-source time-match helper (port of
`select_default_chart_id` semantics: nearest valid time, tie-break earlier
offset). On `synHour` change in chart mode: cheap `updateChartImage(url)` when
the projection key is unchanged, full `setBasemap('chart', …)` rebuild when the
`chart_type` changes (e.g. DWD `ana` ↔ `icon`). Info bar
shows source + chart valid time + gap from Hewson valid ("Met Office +48h from
12Z · 1 h gap"). URL state `syn.base` ∈ `osm|dwd|metoffice`. Per-source
attribution control. Disable a source when its manifest has no cached cycle or
the Hewson valid time is outside its chart horizon.

### Phase 6 — Polish + verification

- Programmatic: EGLL/EDDF/LFPG/LEMD via `makeChartProjection` (each chart type)
  vs `lonlat_to_chart_pixel` Python — match within 1 px.
- Visual: a `frontal/cli.py` baseline frontal case — high-|∇θe| band + gate
  polylines align with the chart's drawn fronts, on **both** DWD and Met Office.
- Hover parity OSM vs chart; mobile tap-hover; theme (charts have no dark
  variant — legend/hover boxes still theme-aware); first-load cost of the heavy
  DWD analysis PNG (immutable cache; consider prefetch on toggle hover).

## Commit plan

1. Extract `ChartSource` base; refactor dwd/metoffice modules onto it (shims).
2. Calibration dump script + generated TS constants + JSON fixture.
3. TS projection port (fwd+inv) + equivalence test.
4. Unified `synoptic_charts` router (manifest + serve); refactor packs.py to
   shared serve helper.
5. HewsonGridLayer pluggable projector.
6. SynopticMap chart-basemap mode (grid + fronts + hover).
7. maps-main 3-way picker, time-match, info bar, URL state, attribution.
8. Polish + edge cases.

## Implementation status (verified 2026-06-13)

Phases 0–5 implemented and **on `main`** (the `chart_cache.py`,
`synoptic_charts.py`, `chart-projection.ts`, etc. files all exist on main; the
`feat/synoptic-chart-basemap` branch itself is *not* an ancestor of main, so the
work landed via separate commits — see `730770b2 refactor(charts): extract
shared ChartCache base`). All unit / integration tests green at the time (314 py
chart/pipeline, 342 web incl. projection equivalence <1px). Phase 6 =
in-browser visual verification (user-driven).

Re-verified 2026-08-15: every claimed file still exists on main and the
as-built details above match the code.

This is effectively built; the remaining content is the durable architecture
note (composable `ChartCache`, two-source gating, projection equivalence
contract) worth folding into a real design doc rather than leaving as a plan.
Note the natural home is ambiguous today: `designs/fetch.md` already redirects
readers of `chart_cache.py` to `frontal-detection.md`, but that doc only covers
charts as *calibration references* (`_dwd_lonlat_to_pixel`, `validate`/`charts`
CLI) and says nothing about `ChartCache`, the `synoptic-charts` endpoints, or
the chart-basemap renderer. Promotion should close that gap rather than leave a
dangling pointer.

Two items deferred from the plan, by choice:
- **URL state `syn.base`** — skipped to stay consistent with the existing
  decision that the synoptic tab's *inner* controls aren't deep-linked (only
  `tab` is). Easy to add later if we deep-link the rest.
- **Gap-based disable of far-out charts** — instead of disabling the toggle
  when the Hewson valid time is beyond a source's horizon, we always render the
  closest chart and surface the gap in the info line (`… 36 h gap from Hewson
  valid time`). Simpler and still honest; revisit if the mismatch confuses.

## Open (non-blocking) questions

- Met Office `colour` is 800×540 — blurry zoomed in; cap `maxZoom`. Same as DWD
  `icon`. Acceptable (source resolution).
- Should the picker remember the last source per session? Still open — the
  *manifest* is cached per session, but the chosen source is module state and
  resets to OSM on reload (no URL state either, see deferred items above).
- Default chart source: **settled by construction** — the map always opens on
  OSM, and chart buttons follow `SOURCES` insertion order in
  `_chart_serving.py`, i.e. DWD before Met Office. No explicit default needed.
