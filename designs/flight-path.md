# Flight path: climb, cruise and descent per aircraft (#1005)

> Status: phase 1 shipped behind `WB_FLIGHT_PATH` (default `shadow`). Consumers:
> aircraft icing and cloud / VMC. The path flies the pooled forecast wind
> (#1007). Phase 2 (turbulence, winds aloft, convective, fronts, time scan
> grading) is not built.

## Intent

Before #1005 every route point was treated as being at `cruise_altitude_ft`,
and point times were linear in distance. The only climb or descent anywhere
was the icing card's two endpoint columns at 500 fpm. A briefing now carries
**one** climb / cruise / descent path, computed on the server from the
aircraft, so the phase-1 cards read the weather where and when the aircraft
actually is. Nothing is computed on a client.

## Structure

| Piece | Where | What |
|---|---|---|
| Library | `analysis/flight_path.py` | Presets, `resolve_performance`, `compute_flight_path`, `recompute_for_cruise`, `estimate_duration_h`, `flight_path_mode`, `build_flight_path` / `route_flight_path` / `analyses_flight_path` builders. Pure, no `tasks/` imports. |
| Models | `models/flight_path.py` | `AircraftPerformance`, `FlightPath` (+ `FlightPathPoint`, `PathSlice`, `VerticalPart`), `FlightPathSummary`. |
| Speeds | `atmo.py` | `cas_to_mach_isa`, `mach_to_tas_isa`, `cas_to_tas_isa`, ISA T / p. Server only (no `atmo.ts` mirror). |
| Aircraft | `user_aircraft` (migrations 119, 120) | `performance_preset` (the preset picked in the form; NULL = follow the ICAO type), `is_turbo`, `climb_ias_kt`, `climb_rate_fpm`, `descent_rate_fpm`, `descent_angle_deg`, all optional. `storage.aircraft.aircraft_performance(row)` resolves. API returns them and the resolved `performance`; PUT with 0 clears an override, `performance_preset: ""` goes back to the type. `GET /api/aircraft/presets?icao_type=&cruise_speed_kt=` gives the form the preset table and the type's suggestion. |
| Equipage | `AircraftEquipage.performance` | Set by `api/packs._resolve_equipage`; the placeholder gets the unknown-aircraft default with its own cruise speed. |
| Pipeline | `tasks/analyze.run_analysis` | Builds the path (`route_flight_path`), stamps `RoutePointAnalysis.planned_altitude_ft` / `phase`, stores `RouteAnalysesManifest.flight_path` + `flight_path_mode`. Live: `interpolated_time` from the path. |
| Advisories | `tasks/advise.py` | `RouteContext.flight_path` / `flight_path_live`; recalculation reuses the stored path (`recompute_for_cruise` on a cruise override) or builds one for old packs; alt departure and time scan build it from the route points. `RouteAdvisoriesManifest.flight_path` is the summary (TOC / TOD / live). |
| Card helpers | `analysis/advisories/_flight_path.py` | `with_path_shadow`, `record_path_shadow`, `flown_band`, `path_rate_limit`, `in_cloud_on_path`, `in_cloud_sentence`. |
| Duration | `POST /api/flights/duration-estimate` | The new-flight form's estimate on all three clients (web `recalcDurationEstimate`, iOS `AddFlightViewModel.refreshDurationEstimate`, Android `AddFlightViewModel`), rounded up to the quarter hour. A pilot's duration wins, as does one from an FPL or a copied flight. On failure: web a local cruise-TAS estimate, Android its distance / speed + 0.25 h, iOS keeps the field as it is (it never had a local estimate). |
| Display | web `reference-lines.ts`, iOS `ReferenceLinesLayer`, Android `CrossSectionCanvas` | The cruise line follows the path, only when the pack's mode is `live` and the view is at the planned cruise (`CrossSectionViewModel.drawnFlightPath`, `FlightPathDisplay.pathToDraw`). Otherwise the flat line. |
| Digest | `prompt_builder._flight_path_header_line` | `FLIGHT PATH:` TOC / TOD line, live only. |

## The model

- **Climb** at the preset climb IAS (jets: 250 kt below FL100, then CAS until
  the Mach cap). Rate falls linearly from `climb_rate_low_fpm` (sea level) to
  `climb_rate_high_fpm` (ceiling); a turbo piston holds the low rate to its
  critical altitude. Rate floor 100 fpm.
- **Cruise** at the aircraft's cruise IAS → TAS (jets: also capped at the
  preset cruise Mach). Unknown cruise IAS: a per-preset typical value.
- **Descent** at a fixed rate (pistons) or a 3° path (turbine), capped, flown
  at the cruise speed schedule.
- Integrated in 100 ft steps, kept every 500 ft in `FlightPath.profile`
  (`[distance_nm, altitude_ft, time_s]`), interpolated onto the route points.
- **Short leg:** climb and descent meet at `peak_ft` below cruise
  (`cruise_reached=False`, `toc_nm == tod_nm`).
- **Duration:** the path's own still-air duration is what the form shows.
  The flight's stored duration (the estimate, or the pilot's overwrite) wins:
  `time_scale` stretches the path's times to it. Vertical rates are read
  unscaled (aircraft performance, not wind).
- **Wind (#1007):** `compute_flight_path(..., wind_at=None, wind_grid=None)`,
  `wind_at(distance_nm, altitude_ft) -> headwind_kt`. The pipeline passes a
  `HeadwindGrid` from `analysis/wind.route_headwind_grid`: the models' median
  headwind on the route track, at every route point and every 1,000 ft up to
  max(cruise, ceiling), read at the still-air path's planned times (one pass;
  meteorology-decisions §87). The grid is stored on the path
  (`FlightPath.wind_grid`, `wind: "consensus" | "still_air"`, also on
  `FlightPathSummary`), so `recompute_for_cruise` (altitude table, cruise
  override) flies the same wind without the cross-sections. Builders take
  `cross_sections` + `departure_time` (`route_flight_path`) or the analyses'
  own times (`analyses_flight_path`, which also gives a stored pre-#1007
  still-air path its wind). No winds in the pack: the still-air path,
  unchanged. The alt departure and every time-scan candidate read the wind at
  their own time; the scan builds the candidate's path once and hands it to
  `run_alt_from_pack(flight_path=...)` so coverage and grade agree; the
  ECMWF-only provisional tier builds its path in ECMWF's wind (the other models
  are clamped outside their window). Cost: about 1 ms per route point per grid
  (4 models, 26 levels), once per candidate. The grid ships with the stored
  path (`route_analyses.flight_path`): route points × 1,000 ft levels, a few
  KB per briefing (tens for a jet), kept as long as the pack. The
  new-flight estimate stays still air. One path per briefing, not per model.

## Presets (tune in `PRESETS`)

| id | for | climb IAS | rate low → ceiling | ceiling | descent |
|---|---|---|---|---|---|
| `trainer_piston` | SEP, cruise IAS < 120 | 75 | 700 → 100 | 13,000 | 500 fpm |
| `hp_piston` | SEP, unknown aircraft | 110 | 1,000 → 100 | 17,500 | 500 fpm |
| `turbo_piston` | SEP + turbo | 120 | 1,000 flat to 18,000 → 300 | 25,000 | 700 fpm |
| `twin_piston` | MEP | 120 | 1,300 → 100 | 20,000 | 700 fpm |
| `single_turboprop` | SET | 150 | 2,000 → 500 | 31,000 | 3°, cap 2,500 |
| `twin_turboprop` | MET | 160 | 2,500 → 500 | 35,000 | 3°, cap 3,000 |
| `light_jet` | JET (not advertised) | 250 / 260 / M0.70 | 3,500 → 500 | 45,000 | 3°, cap 4,000 |

Overrides: climb IAS, climb rate (replaces the sea-level rate; the
near-ceiling rate keeps the preset's proportion), descent rate or angle (angle
wins), the aircraft's service ceiling. A value equal to the preset's own is not
an override.

**Picked preset and turbo.** The web aircraft form has a preset dropdown over
a compact grid of its values (climb speed, climb rate, descent rate or angle,
Turbocharged). Editing a value away from the preset shows "Custom (based on
…)"; picking a preset restores its values. It saves the base preset
(`performance_preset`, `source="preset"`) plus only the values that differ
(`source="aircraft"` when any does). With a picked preset, turbo is a value
like the others: ticked on a non-turbo preset it holds the sea-level climb rate
to `TURBO_CRITICAL_ALT_FT` (18,000 ft, 80% of a lower ceiling), unticked on
`turbo_piston` the rate decays from sea level. Without a picked preset (older
rows, app versions before #1008), the type picks it and `is_turbo` selects the
turbo piston for an SEP. A preset that follows the type moves with the type and
cruise speed in the form; a picked or custom one stays. The form logic is
`web/ts/components/aircraft-performance-form.ts`, ported to iOS
(`AircraftPerformanceForm`, `AircraftFormModel`) and Android
(`AircraftPerformanceForm`, `AircraftPerformanceFormController`). The preset
values always come from `GET /api/aircraft/presets`. If that call fails the
section stays closed and a save omits every performance field, so saved
overrides survive; the apps retry the open on the next type or speed change.

## Phase-1 consumers

- **Aircraft icing** (`aircraft_icing.py`): in path mode each point's cell is
  split by `RouteContext.path_slice` into its climb, level and descent parts.
  The level part reads the cruise band around the path level; each vertical
  part the zones its altitude band crosses at that point (`PathIcing`).
  Vertical minutes = iced thickness / path rate, × 1 / `ICED_RATE_FACTOR`
  (0.7) for non-FIKI, clean rate for FIKI. Stretches record where each phase
  is (`_Stretch.spans`) and the sentence names it ("climb, nm 3 to 14",
  "descent, 30 to 12 nm out"). SLD hits use the same parts. The flat endpoint
  columns stay for flat mode.
- **VMC at cruise / cloud tops**: a cell is graded on its flown band
  (`flown_band`), not at cruise. Live VMC text (#1011) never says "at cruise":
  the headline names the phases whose cells fly in cloud
  (`cell_phases_in_cloud`, `phase_list`: "IMC on the climb, cruise and descent
  over ..."; clear = "Clear on the" + `path_phases`). The climb / descent
  minutes from `in_cloud_on_path` follow as "; this includes the climb in cloud
  from nm X to nm Y, about N min": those cells are already in the headline
  extent, so the sentence is a breakdown, never extra time to add. Under the
  OVC (red) headline the breakdown counts OVC layers only, so "includes" stays
  true. The two minute figures measure different things (headline = time over
  the flagged distance at cruise groundspeed; breakdown = cloud thickness / path
  rate). Flat mode keeps the "at cruise" strings. Cloud tops text has no
  "at cruise" wording and is unchanged; only its catalog description mentions
  the band.
- **Solver**: `vertical_profile.solve(rate_limit=path_rate_limit(ctx))` in the
  VFR and icing mitigations, so a suggested profile is never steeper than the
  aircraft's mean planned climb / descent gradient (one bin step always
  allowed).
- **Altitude table**: each row is flown on the same aircraft's path to that
  altitude (`recompute_for_cruise`), live only.

## Rollout: `WB_FLIGHT_PATH`

- `shadow` (default): path built and stored, points stamped, **times linear**,
  cards grade flat and record the path grade per model as
  `ShadowGrade(rule="flight_path")` (status, detail, reason `same` / `differs`
  or the icing reason). The icing shadow grades only (no solver).
- `live`: point times from the path, phase-1 cards grade on it, web draws it,
  digest gets the `FLIGHT PATH` line.
- `off`: no path at all, exactly as before #1005.

The mode is read at evaluation time, so `WB_FLIGHT_PATH=live
scripts/rerun_advisories_diff.py` replays stored packs on a path (old packs
get one built from their analyses; their point times stay linear).

**Before flipping to `live`** (from the #1006 review):
- Compare the replay **per model**: `ICED_RATE_FACTOR` and the presets are
  model-independent, but a cell's iced thickness comes from each model's
  pressure-level spacing, so the climb minutes in an iced layer are coarser
  on the sparse-level models.

## Choices and gotchas

- **Extent denominator:** climb / descent cells stay in the route-extent
  denominator and are graded at their own band (meteorology-decisions §85).
- **A cell's band, not a point altitude:** a climb cell is graded on every
  altitude it passes through, so a thin layer between two points is not
  missed. This is more conservative than reading the point altitude.
- **VFR feasibility en route** still grades at cruise: its corridor axis
  already covers decks near the airports; only its solver reads the path.
  `ifr_feasibility` inherits the icing grade (path-aware when live).
- The shadow costs one extra grade per phase-1 card per model.
- `FlightPath.point(i)` keys on the route-point index; analyses may skip
  points, so read cells through `RouteContext.path_slice(idx)` (analyses
  index), never by position in `points`.
- Shared app fixtures: `app/fixtures/api/route-analyses-live.json` (a live path
  from `compute_flight_path`) and `aircraft-presets.json` (the server's table).
  Mock mode still serves the flat `route-analyses.json`, so the journeys don't
  depend on the path.
