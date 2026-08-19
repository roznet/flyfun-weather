# Known Issues & Things to Revisit

Items to periodically review. When resolved, move to the bottom under "Resolved".

---

## Bundle endpoint memory usage

**Added:** 2026-03-27
**Location:** `src/weatherbrief/api/packs.py` — `get_bundle()`

The bundle endpoint builds the entire JSON response in memory before gzip-compressing it. For long flights (~60 route points x 3 models = 180 sounding profiles), the worst case involves loading `cross_section.json` (can be tens of MB) fully into memory and holding the full JSON dict + serialized payload + gzip output simultaneously.

**Partially mitigated (the sounding-profile cost):** `get_bundle()` now prefers a gzipped sounding sidecar written at refresh time (`read_sounding_sidecar` from `storage/sounding_profiles.py`; written by `tasks/artifacts.py` → `_write_sounding_sidecar`) instead of building all ~180 profiles on the fly. Only packs that predate the sidecar fall back to `build_sounding_sidecar(ra_data, cs_data)` (which still re-reads `cross_section.json` and rebuilds every profile). So the heavy MetPy/Pydantic recompute is gone for the common path; what remains is the in-memory assembly + gzip of the whole bundle.

For the short flights tested so far (8 points, 472KB uncompressed, 47KB gzip) this is fine. For very long routes the uncompressed bundle could still reach 50-60MB, meaning ~100-200MB transient memory per concurrent request.

**Options if the remaining in-memory assembly becomes a problem:**
- Stream the gzip output instead of building in memory
- Pre-compute and cache the *entire* bundle on disk after each refresh (the sounding-sidecar precompute is a first step in this direction)
- Limit concurrency on this endpoint (similar to existing `plot_limiter`)

---

## SLD collision-coalescence mechanism disabled

**Added:** 2026-03-28
**Location:** `src/weatherbrief/analysis/sounding/sld.py` — `_coalescence_sld_zones()` (code retained, call commented out)

The SLD layer has two physical detection mechanisms: warm-nose freezing rain (active) and collision-coalescence in deep warm-top clouds (disabled).

**What we found:** The coalescence criteria (cloud depth > 3000ft, cloud-top temp > -12C, overlapping the freezing level) fire on virtually every deep stratiform cloud in European winter/spring weather. Testing on EGTF-BILGO-DJL-LSGS (2026-04-03) produced 59 SLD zones across the route — nearly every route point — from ordinary icing clouds with tops at -2 to -8C. These are not SLD conditions; they're normal mixed-phase clouds where the Bergeron process hasn't yet glaciated the liquid. The mechanism cannot distinguish "big drops from coalescence growth" from "normal small cloud droplets" without droplet size information.

**Root cause:** NWP models (GFS, ECMWF, ICON) output cloud liquid water mass (CLMR/ICMR) but not droplet size distribution. Collision-coalescence produces large drops (>50um) but so does any cloud — the difference is the drop size spectrum, which we can't observe from mixing ratios alone. The atmospheric structure (deep + warm top) is necessary but far from sufficient.

**Ideas to make it work in the future:**
1. **Require CLMR corroboration with a much higher threshold** — e.g. >0.3 g/kg (vs current 0.05). Very high CLMR values may indicate drizzle-size drops from coalescence, but this needs validation against observed SLD events.
2. **Cross-reference with NWP precipitation type** — GFS outputs categorical precipitation type (rain/snow/FZRA/ice pellets). If the model itself forecasts FZRA at the surface, that's a strong SLD signal independent of the warm-nose detection.
3. **Satellite-derived cloud-top phase** — GOES/Meteosat provide cloud-top particle effective radius. Liquid-topped clouds with large effective radius (>15um) correlate with active coalescence. Would require ingesting satellite data.
4. **Require the supercooled portion to be substantial** — instead of checking total cloud depth, require the subfreezing portion (freezing level to cloud top) to be >2000ft. A 4000ft cloud with only 50ft above the freezing level doesn't produce SLD.
5. **Use CIP/FIP SLD algorithm** — the operational FAA Current Icing Product uses a decision tree that combines model temperature, RH, vertical velocity, and satellite data. Would require significant additional data sources.

For now, only warm-nose freezing rain is active — it's the high-confidence SLD signal where large drops are guaranteed by physics (rain drops are 0.5-5mm by definition).

**Related and also deliberate — SLD output is not wired into the advisories.** `assess_sld_zones()` fills `sounding.sld_zones`, but nothing populates `IcingZone.sld_risk`, so the `sld_risk` branches in `advisories/_helpers.py`, `fiki_icing.py` and `icing_escape.py` are dormant by design (the contract is written so that "sld_risk survives a NONE risk" is already the safe default *if* it is ever wired up). Do NOT "clean up" those unreachable clauses — the wiring is on hold pending validation of the warm-nose signal end-to-end, not forgotten.

---

## ECMWF GRIB ceiling field is inherently sparse

**Added:** 2026-04-14
**Location:** `src/weatherbrief/fetch/grib/decode.py` — `decode_ecmwf_surface_per_point()` / `build_ecmwf_cloud_diagnostics()`

The ECMWF `ceil` (ceiling) field in the a1 surface GRIB is ~50% NaN. ECMWF only populates ceiling where there is significant cloud cover — clear or scattered areas have no value. Our bilinear interpolation returns None when any of the 4 surrounding grid points is NaN, so route points near the edge of a cloud mass often lose the ceiling even when a neighbouring grid cell has a value.

**Observed:** Testing 20260413 HRES data, EGTF (51.35°N, -0.56°W) falls in a NaN gap despite a valid ceiling one grid cell away (6952m). KJFK shows ceiling via `cbh` (cloud base height, which is dense) but not via `ceil`.

**Decision:** Accept this as-is. Ceiling being absent where skies are mostly clear is meteorologically correct — there is no meaningful ceiling to report. The `cbh` (cloud base height) and per-level `cc` (cloud cover fraction on pressure levels) provide cloud information at those points regardless.

**Since then (2026-08, still consistent with the above):** two things about `ceil`/`cbh` are now pinned in the decoder, so "no ceiling at this point" has *two* distinct causes and you should not confuse them when debugging:
- **9999 m is an explicit ECMWF "no cloud" sentinel**, dropped in `build_ecmwf_cloud_diagnostics`'s `_agl_m` helper (`_ECMWF_NO_CLOUD_SENTINEL_M`). That path is *not* the sparse-field problem — the model is positively saying "clear".
- **`ceil`/`cbh`/`hcct` are metres AGL** (referenced to the model's own orography, #487), not MSL and not gpm. `deg0l` is AGL too and is the one field allowed to go negative.

The NaN-gap case described above is the remaining one: `_interpolate_per_point` does bilinear via `xarray.interp` and maps NaN → None, so any of the 4 surrounding grid cells being NaN loses the point.

**Revisit if:**
- Pilots report missing ceiling data in overcast conditions (would indicate a real gap, not a sparse-field artifact)
- We want to switch `ceil` interpolation to `nearest` for better coverage at the cost of spatial precision

---

## Resolved

### GFS cloud-diagnostic forward-fill produced phantom layers

**Added:** 2026-05-12
**Resolved:** 2026-05-13 (issue #148, PR #149)
**Location:** `src/weatherbrief/fetch/grib/fill.py` —
`_fill_cloud_diagnostics`, `_fill_cloud_water`,
`apply_gfs_rh_condensate_gate`

GFS LCDC/MCDC/HCDC are averaged-window fields (1-6 h windows: NCEP resets
the window at every multiple of 6 and lets it grow to the next reset, so
past f120 the widths alternate 3 / 6. This write-up originally said
"1/2/3 h ... always 3 h past f120" — that model was disproven in #480). Past
f120 the 3-hourly cadence meant gap hours were forward-filled from the
preceding native step, smearing each window's average forward up to 2 h
beyond where it applied. At pt11 of flight
`lfrq_ercoz_jsy_revtu_tujag_rudmo_egtf-2026-05-17` this produced a 100 %
mid deck at 14:00 Z (FL180–222) that no instantaneous signal supported
(RH 11–26 %, CLMR + ICMR = 0, GRAMET clear).

Fix replaces the forward-fill with window-midpoint linear interpolation
for GFS cloud diagnostics (`_interp_gfs_diag_hourly`) and adds an
RH/condensate gate (`apply_gfs_rh_condensate_gate`) that drops any band
whose pressure-level RH and condensate inside `[base_ft, top_ft]` don't
support the averaged cover. ICON-EU / ECMWF publish instantaneous cover
and are unaffected. See [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate)
for the full rationale.

The longer-term cleanup — drop the averaged MCDC entirely and re-derive
cloud cover from instantaneous RH + condensate (Sundqvist-style) — is
deliberately deferred as a separate follow-up.
