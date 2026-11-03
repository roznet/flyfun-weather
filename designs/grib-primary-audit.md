# GRIB-Primary Decode & Interpolation Audit

> Comprehensive technical review of GRIB reading, message selection, unit/sign conversions, de-accumulation, and spatial, vertical, and temporal interpolation across all models (`ECMWF`, `ICON-EU`, `ICON-D2`, `GFS`, `HRRR`, `AROME`) and all three pipelines (**route briefings**, **standalone map/alternates snapshots**, and **Hewson frontal detection**).

---

## 1. Bugs & Subtle Discrepancies Found

### 1.1 Coarse-step (3h / 6h) upper bracket across `00:00Z` is dropped by `build_skeleton`, causing 100% Open-Meteo fallback for late-evening flights
* **Severity:** High (silent 100% Open-Meteo fallback + un-interpolated soundings at `22:00Z` / `23:00Z`)
* **Files:**
  - [`src/weatherbrief/fetch/grib/primary.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L120-L170) (`build_skeleton`, `fetch_end_date`)
  - [`src/weatherbrief/fetch/grib/grib_fetch.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/grib_fetch.py#L174-L223) (`contiguous_window_forecast_hours`)
  - [`src/weatherbrief/fetch/grib/__init__.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L4696-L4737) (`_select_ecmwf_window_steps`, `_replace_pressure_levels_from_grib`, `_apply_cloud_diagnostics_to_sections`, `_apply_ecmwf_surface_to_hourly`, `_write_surface_step`)
  - [`src/weatherbrief/fetch/grib/fill.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L1230-L1375) (`propagate_all`, `_interp_surface_hourly`, `_interp_levels_hourly`)
* **Mechanism:**
  1. When a flight falls in a model's 3-hourly region (`ICON-EU` > +78h, `ECMWF` > +90h, `GFS` > +120h) or 6-hourly region (`ECMWF` > +144h) and its window ends at `22:00Z` or `23:00Z` on day `D` (for example, departure `20:00Z`, duration `2h`, so `required_hours` ends at `22:00Z` on day `D`), [`fetch_end_date`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L166-L170) returns `day D`.
  2. [`build_skeleton`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L120-L148) creates `HourlyForecast` slots for `00:00Z .. 23:00Z` through `end_date` (`day D`), so `wf.hourly` ends at `day D 23:00Z`.
  3. During GRIB enrichment, [`contiguous_window_forecast_hours`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/grib_fetch.py#L174-L223) (GFS/ICON) and [`_select_ecmwf_window_steps`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L4696-L4737) (ECMWF) rightly compute the upper bracketing native step at `day D+1 00:00Z` (or `03:00Z`), and download + decode that GRIB step.
  4. When `_replace_pressure_levels_from_grib`, `_apply_cloud_diagnostics_to_sections`, `_apply_ecmwf_surface_to_hourly`, and `_write_surface_step` loop over `wf.hourly` looking for `_matches_valid_time(h.time, valid_utc)` at `day D+1 00:00Z`, **there is no `HourlyForecast` slot for `day D+1 00:00Z` in `wf.hourly`**. The upper bracket's sounding, cloud diagnostics, and instantaneous surface values are silently dropped (only rate fields on `(21:00Z, 00:00Z]` match `22:00Z` and `23:00Z`).
  5. Next, [`_interp_surface_hourly`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L1301-L1333) and [`_interp_levels_hourly`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L938-L995) interpolate only between two populated anchors in `wf.hourly`. Because `21:00Z` has no upper anchor in `wf.hourly`, **`22:00Z` and `23:00Z` are left with `pressure_levels = []` and `temperature_2m_c = None`**.
  6. Finally, [`point_window_covered`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L173-L198) finds `22:00Z` uncovered at every route point, triggering a **100% Open-Meteo fallback** across the entire route — and even after fallback, `22:00Z` and `23:00Z` keep Open-Meteo soundings instead of time-interpolated GRIB soundings despite the `00:00Z` GRIB step having been downloaded and decoded.
* **Recommended Fix:** Extend `fetch_end_date` (or `build_skeleton`) by `+6 hours` before taking `.date().isoformat()` so `wf.hourly` always contains the `day D+1` hours needed to hold a 3h/6h upper bracket.

---

### 1.2 GRIB-primary GFS briefings leave `HourlyForecast.cloud_cover_{low,mid,high}_pct` as `None` at all `fhour > 0`
* **Severity:** Medium-High (`HourlyForecast` low/mid/high cloud cover fields are always `None` on GRIB-primary GFS briefings)
* **Files:**
  - [`src/weatherbrief/fetch/grib/gfs_idx.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/gfs_idx.py#L416-L460) (`GFS_SURFACE_FIELDS`, `_gfs_surface_step_matches`, `plan_gfs_surface_byte_ranges`)
  - [`src/weatherbrief/models/analysis.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/models/analysis.py#L580-L606) (`HourlyForecast.attach_nwp_diagnostics`)
  - [`src/weatherbrief/tasks/standalone_verification.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1685-L1691) (`_gfs_rows`)
* **Mechanism:**
  1. In [`GFS_SURFACE_FIELDS`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/gfs_idx.py#L416-L419), `LCDC`, `MCDC`, and `HCDC` use [`_gfs_surface_step_matches`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/gfs_idx.py#L437-L445), which requires instantaneous `"N hour fcst"`.
  2. For `fhour > 0`, NOAA GFS `pgrb2` only publishes `LCDC:low cloud layer`, `MCDC:middle cloud layer`, and `HCDC:high cloud layer` as `"0-N hour ave fcst"` (never `"N hour fcst"`, as pinned in [`tests/test_grib_primary_gfs.py`](file:///Users/brice/Developer/public/flyfun-weather/main/tests/test_grib_primary_gfs.py#L155)). Consequently, `build_gfs_surface_snapshot` always sets `cloud_cover_low_pct`, `cloud_cover_mid_pct`, and `cloud_cover_high_pct` to `None` for `fhour > 0`.
  3. Meanwhile, `_enrich_cloud_diagnostics` + [`_interp_gfs_diag_hourly`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L225-L346) + [`apply_gfs_rh_condensate_gate`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L1144-L1209) compute the de-averaged and RH/condensate-gated low/mid/high cloud covers on `hourly.nwp_cloud_diagnostics.{low,mid,high}.cover_pct`.
  4. On ICON briefings, [`_icon_diag_surface_fields`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L5589-L5600) copies `diag.{low,mid,high}.cover_pct` onto `HourlyForecast.cloud_cover_{low,mid,high}_pct`. On GFS standalone snapshots, [`_gfs_rows`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1685-L1691) also manually backfills `hourly.cloud_cover_{low,mid,high}_pct` from `cd.diagnostics`.
  5. On **GFS briefings**, however, nothing copies `nwp_cloud_diagnostics.{low,mid,high}.cover_pct` onto `HourlyForecast.cloud_cover_{low,mid,high}_pct` when those fields are `None` (because [`attach_nwp_diagnostics`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/models/analysis.py#L597-L605) deliberately avoided overwriting Open-Meteo's values when Open-Meteo was always underneath).
* **Recommended Fix:** In `apply_gfs_rh_condensate_gate` (or `attach_nwp_diagnostics` when `self.cloud_cover_{low,mid,high}_pct is None`), backfill `cloud_cover_{low,mid,high}_pct` if currently `None`.

---

### 1.3 Step 0 (`f000`) in GRIB-primary briefings fails `point_window_covered` due to `precipitation_mm = None` (and ICON `vmax_10m = 0.0` at `f000`)
* **Severity:** Medium (triggers 100% Open-Meteo fallback whenever `f000` is in `required_hours`; writes `0.0 kt` gust at `f000` on ICON briefings)
* **Files:**
  - [`src/weatherbrief/fetch/grib/primary.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L173-L198) (`point_window_covered`)
  - [`src/weatherbrief/tasks/standalone_verification.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1766-L1770) (`_icon_rows`, `_grib_hour_covered` lines 1857–1871)
  - [`src/weatherbrief/fetch/grib/__init__.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L5836-L5854) (`_enrich_icon_eu_cloud_diagnostics`)
* **Mechanism:**
  - At `fhour == 0` (analysis step), there is no prior step to difference accumulated precipitation against (`icon_eu_previous_step(0) -> None`, `_gfs_previous_step(0) -> None`, `prev_a1_valid_utc = None` on ECMWF), so `precipitation_mm` is `None`.
  - In standalone snapshots, [`_grib_hour_covered`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1857-L1871) explicitly exempts `precipitation_mm` when `analysis_step=True`.
  - In briefings, [`point_window_covered`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/primary.py#L173-L198) requires `precipitation_mm is not None` for every hour in `required_hours`. If a flight window (or an `as_of_time` / near-now departure) includes the model's `f000` valid hour, `precipitation_mm is None` at `f000` causes **every point on the route to be marked uncovered** and refetched from Open-Meteo.
  - Additionally, in [`_icon_rows`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1766-L1770), `wind_gusts_10m_kt` is cleared to `None` at `fhour == 0` because DWD `vmax_10m` is a window maximum that is zero at init (`0-0h`), whereas [`_enrich_icon_eu_cloud_diagnostics`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L5836-L5854) writes `0.0 kt` at `fhour == 0` on briefings.

---

### 1.4 Inconsistent `rain_mm` / `showers_mm` / convective-snow partitioning across ECMWF, ICON, and GFS
* **Severity:** Low-Medium (convective snow double-counted or double-subtracted between `rain_mm`, `showers_mm`, and `snowfall_cm`)
* **Files:**
  - **ECMWF:** [`src/weatherbrief/fetch/grib/__init__.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L4374-L4379) (`_apply_ecmwf_surface_to_hourly`)
  - **ICON:** [`src/weatherbrief/fetch/grib/__init__.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L5575-L5586) (`_icon_surface_rates`)
  - **GFS:** [`src/weatherbrief/fetch/grib/decode.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L3375-L3450) (`gfs_frozen_fraction`, `gfs_surface_rates`)
* **Mechanism:**
  1. **ECMWF double-subtracts convective snow from `rain_mm`:**
     In IFS GRIB (`a1`), `tp` is total precipitation (`lsp + cp`), `cp` is total convective precipitation (`convective rain + convective snow`), and `sf` is total snowfall (`large-scale snow lssf + convective snow csf`).
     Line 4378 computes:
     $$\text{rain\_mm} = \max(0, \Delta\text{tp} - \Delta\text{cp} - \Delta\text{sf})$$
     Because both $\Delta\text{cp}$ and $\Delta\text{sf}$ include convective snow ($\Delta\text{csf}$), convective snow is subtracted **twice**, reducing large-scale liquid `rain_mm` by $\Delta\text{csf}$ and counting $\Delta\text{csf}$ in both `showers_mm` ($\Delta\text{cp}$) and `snowfall_cm` ($0.7 \times \Delta\text{sf}$).
  2. **ICON counts `snow_con` in both `showers_mm` and `snowfall_cm`:**
     [`_icon_surface_rates`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/__init__.py#L5581-L5585) sets `showers_mm = rain_con + snow_con` and `snowfall_cm = (snow_gsp + snow_con) * 0.7`. In Open-Meteo's DWD ICON mapping, `showers` is liquid convective rain (`rain_con`) only, while `snow_con` goes only into `snowfall`.
  3. **GFS `CPOFP` at end-of-window vs window-accumulated `APCP`:**
     In [`gfs_surface_rates`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L3375-L3450), `APCP` and `ACPCP` are accumulated over `(prev, valid]` (1h or 3h), while `CPOFP` (`percent frozen precipitation`) and `CSNOW` are instantaneous at `valid`. When precipitation occurred earlier in the 1h/3h window but stopped before `valid`, NOAA sets `CPOFP = -50.0` (missing sentinel) and `CSNOW = 0.0` at `valid`. [`gfs_frozen_fraction`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L3390-L3396) then falls back to `CSNOW = 0.0`, classifying a snow shower that ended before the hour mark as 100% liquid rain even if `T_2m < 0°C`.

---

### 1.5 Standalone GFS snapshots attach raw 6-hour-averaged `NWPCloudDiagnostics` without de-averaging or RH/condensate gating
* **Severity:** Medium (map/verification GFS snapshots can suffer from GFS 6-hour rolling-average phantom cloud decks, issue `#148`)
* **Files:**
  - [`src/weatherbrief/tasks/standalone_verification.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1614-L1692) (`_fetch_gfs_lookup`, `_gfs_rows`)
  - [`src/weatherbrief/fetch/grib/fill.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L225-L346) (`_interp_gfs_diag_hourly`, `apply_gfs_rh_condensate_gate`)
* **Mechanism:**
  - In [`_fetch_gfs_lookup`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1614-L1622), `fetch_gfs_cloud_diag` fetches GFS's `"0-N hour ave fcst"` rolling 6-hour window averages for `LCDC`/`MCDC`/`HCDC` and their cloud top/base pressures at the sampled map hours (`06, 09, 12, 15, 18Z`).
  - In [`_gfs_rows`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1681-L1692), those rolling 6-hour averages are attached directly to `hourly.nwp_cloud_diagnostics` (and copied to `hourly.cloud_cover_{low,mid,high}_pct`) and passed straight into `analyze_sounding_lite` **without** running [`_interp_gfs_diag_hourly`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L225-L346) (de-averaging + midpoint resampling) or [`apply_gfs_rh_condensate_gate`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/fill.py#L1144-L1209).
  - As a result, a cloud deck at hour `f001` that cleared by `f002` still appears at `f006` in the GFS map/verification snapshot because `f006` carries the `0-6h` average.

---

### 1.6 Standalone ICON-D2 snapshots did not fetch explicit-convection diagnostics (fixed, #953)
* **Status:** Fixed by PR #954 (`meteorology-decisions.md` §71). D2 runs from `2026-10-30 03Z` store `nwp_conv_method="nwp_explicit"`. Rows written before that keep their false grade.
* **Severity:** Low-Medium (D2 map/alternates rows recorded a false `nwp_conv_risk="none"`, which ICON's NWP-track convective verification scored as forecasting no convection; map display, briefings and the thermodynamic track were not affected)
* **Files:**
  - [`src/weatherbrief/tasks/standalone_verification.py`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/tasks/standalone_verification.py#L1711-L1740) (`_fetch_icon_d2_lookup`, `_icon_rows`)
* **Mechanism:**
  - When D2 overrides EU on map/alternates snapshots (`#928` / `#936`), `_fetch_icon_d2_lookup` fetched only D2's cloud diagnostics. `ICON_D2_EXPLICIT_CONV_VARIABLES` were not fetched, so `hourly.explicit_convective_diagnostics` was `None`.
  - In briefings, `_enrich_icon_d2_explicit_convective` attaches `explicit_convective_diagnostics` (`dbz_ctmax`, `echotop`, `lpi_max`, `w_ctmax`, `uh_max`) so the explicit storm path is used.
  - With no payload, [`analyze_sounding_lite`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/analysis/sounding/__init__.py#L401-L409) took `assess_convective_nwp` instead of `assess_convective_explicit`.
  - This was **not** a CAPE fallback, as this section first stated. `convective_scheme_absent` is set only for HRRR and UKV, not ICON-D2, and D2's layer cover and ceiling make `_has_native_cloud_content` true, so [the fallback gate](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/analysis/sounding/convective.py#L835) did not open. With no convective cover, base, top or precipitation, the row landed in the "native model, quiet convective scheme" branch: `method="nwp"`, risk NONE, whatever the model showed.

---

## 2. Verified Sound & Accurate Mechanics

### 2.1 Spatial Interpolation & Grid Geometry
* **Bilinear Grid Interpolation (`decode.py`, `grib_reader.py`):**
  - [`_align_lons_to_axis`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L228-L253) and [`_bilinear_grid_weights`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L261-L343) handle both ascending and descending `lat` axes, `0..360` vs `-180..180` `lon` axes, and cyclic `360°` global wrap-around (bridging `359.75°..0.0°` on GFS).
  - [`combine_bilinear`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/grib_reader.py#L222-L250) skips `w == 0` corners before checking `math.isnan(v)`, so an exact grid-line hit is never NaN-poisoned by a zero-weight masked neighbour.
* **ECMWF Multi-Sub-Grid Selection & `59.5°N–60.0°N` Seam Bridging (`decode.py`):**
  - [`_select_best_subgrid`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L409-L447) selects the finest-resolution covering sub-grid per point (`0.1°` Europe over `0.2°` Nordic over `0.25°`/`0.4°` Atlantic).
  - [`_bridge_lat_seam_points`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L450-L505) linearly blends the 1-cell latitude gap between the top of the Europe sub-grid (`59.5°N`) and the bottom of the Nordic sub-grid (`60.0°N`) in both `a1` surface and `a2` pressure-level decoders, while enforcing `all(level_ok)` for seam points.
* **Lambert Conformal Projection & Wind Rotation (`HRRR`, `AROME`):**
  - [`_lambert_project`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L3514-L3561) uses the GRIB-encoded spherical Earth radius (`6371229 m` on HRRR) and standard parallels (`LaDInDegrees`, `LoVInDegrees`).
  - [`_rotate_lambert_winds`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L3612-L3648) checks `uvRelativeToGrid` (`1` on HRRR → rotates `(u_grid, v_grid)` to `(u_earth, v_earth)` by $\theta = \sin(\phi_0)(\lambda - \lambda_0)$).
  - AROME uses a regular `0.025°` lat/lon grid with earth-relative winds (`uvRelativeToGrid == 0`).
* **ICON-D2 Domain & Validity Mask (`decode.py`, `icon_eu_fetch.py`):**
  - [`decode_icon_d2_validity_mask`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L1943-L1978) and [`points_in_d2_valid_region`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L1904-L1940) require all 4 corners of the bilinear stencil to be finite inside the rotated-pole validity polygon, preventing edge NaNs on both briefings and standalone airport snapshots.
  - ICON-D2 explicit convection (`dbz_ctmax`, `echotop`, `lpi_max`, `w_ctmax`, `uh_max`) uses a 15 nm corridor box reduction (`max` for `dbz_ctmax`/`lpi_max`/`w_ctmax`, `min` pressure for `echotop` across the 4 quarter-hour windows, and signed `argmax |uh|` for `uh_max`).

### 2.2 Vertical Interpolation, Height Datum & Unit Conversions
* **Model-Level to Pressure-Level Interpolation (`ICON-EU`, `ICON-D2`):**
  - [`interpolate_model_levels_to_pressure`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L2517-L2587) sorts levels by ascending `ln(p)` (`top → surface`), interpolates linearly in `ln(p)`, refuses to extrapolate outside `[p_min, p_max]`, and clamps only non-negative fields (`_NON_NEGATIVE_VARS`: `qc`, `qi`, `qr`, `qs`, `qg`, `qv`, `clc`), leaving signed `T`, `u`, `v`, `w` unclamped.
* **Geopotential Height & Datum Reconstruction:**
  - **ECMWF & GFS:** Geopotential $\Phi$ (`z` on ECMWF `a2`, divided by $g_0 = 9.80665$) or `HGT` (`gpm` on GFS/HRRR) used directly.
  - **ECMWF AGL → MSL:** `deg0l`, `hcct`, and `blh` (AGL in IFS) add model terrain height derived from surface pressure `sp` interpolated in `ln(p)` against the model's pressure-level geopotential height profile ([`model_surface_height_m`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L1680-L1735)).
  - **ICON:** [`compute_geopotential_heights`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L2653-L2720) decodes ICON surface `pmsl` first and anchors the hypsometric virtual-temperature integration at `z = 0 m, p = msl_pressure_hpa` when no Open-Meteo levels are underneath (or uses `fi / g0` directly on pressure-level standalone snapshots).
* **Vertical Velocity ($w \leftrightarrow \omega$):**
  - **ECMWF (`w`), GFS (`VVEL`), HRRR (`VVEL`), ICON-EU pressure-level snapshot (`omega`):** Already in $\text{Pa/s}$ ($\omega < 0$ is upward motion).
  - **ICON model levels (`w`) & AROME (`wz`):** Geometric $w$ ($\text{m/s}$) converted to $\omega$ ($\text{Pa/s}$) via $\omega = -w \cdot \frac{p}{R_d T_v}$ in [`w_to_omega`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L2595-L2612) and [`_arome_w_ms_to_omega_pa_s`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L4089-L4103).
* **Humidity & Dewpoint:**
  - [`qv_to_rh`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/decode.py#L2615-L2636) converts specific humidity $q_v$ to WMO liquid-water relative humidity via Bolton (1980) saturation vapor pressure and derives dewpoint via Magnus.

### 2.3 Message Selection, De-accumulation & Time Interpolation
* **GFS `.idx` Planning (`gfs_idx.py`):**
  - [`plan_gfs_surface_byte_ranges`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/fetch/grib/gfs_idx.py#L463-L497) accurately distinguishes instantaneous `"N hour fcst"` (`TCDC`, `TMP`, `UGRD`, `VGRD`, etc.), running totals `"0-N hour acc fcst"` AND `"0-D day acc fcst"` at whole-day multiples (`f024`, `f048`, ..., `f120`, ... for `APCP`/`ACPCP`), takes the first of NCEP's duplicate `APCP`/`ACPCP` pairs (unrounded bucket), and selects the last `HGT:0C isotherm` message (highest freezing level, avoiding low-level winter inversions).
* **De-accumulation (`__init__.py`, `decode.py`, `standalone_verification.py`):**
  - Cumulative totals (`tp`/`cp`/`sf` on ECMWF, `tot_prec`/`rain_gsp`/`rain_con`/`snow_gsp`/`snow_con` on ICON, `APCP`/`ACPCP` on GFS) are differenced against the predecessor step (`every_previous_step=True` for non-contiguous map hours, or leading step + contiguous steps for briefings), divided by `window_h = (valid_utc - prev_valid_utc) / 3600`, and written across `(prev_valid_utc, valid_utc]`.
* **Time Interpolation (`fill.py`):**
  - Linearly interpolates instantaneous surface and sounding scalars between native steps (`3h` / `6h`), decomposes 10m and pressure-level winds into `(u, v)` before interpolating (avoiding the `350° ↔ 010°` wrap-around error), and uses conservative operators (`min` for visibility / ceiling / cloud base, `max` for gusts / cloud top / cover).

### 2.4 Hewson Frontal Detection (`hewson/grib_source.py`)
* [`extract_850_from_grib_bytes`](file:///Users/brice/Developer/public/flyfun-weather/main/src/weatherbrief/hewson/grib_source.py#L150-L246) and model-specific loaders filter strictly on `typeOfLevel=isobaricInhPa` at `850 hPa`, convert `r` (% RH) to specific humidity $q$ ($\text{kg/kg}$) at $850\text{ hPa}$ for ICON/GFS while taking `q` directly for ECMWF, convert geopotential `z` ($\text{m}^2/\text{s}^2 \to \text{m}$) on ECMWF/ICON while keeping GFS `gh` in meters, flip descending latitude axes so `lat` is ascending, and normalize `[0, 360)` longitudes before cropping to the European domain.
