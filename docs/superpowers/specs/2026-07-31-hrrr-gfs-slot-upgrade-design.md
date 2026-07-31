# HRRR full-sounding upgrade of the `gfs` slot — design (issue #457)

**Date:** 2026-07-31
**Status:** Approved design (superpowers brainstorming). Independent implementation —
built from the pre-#508 base (`42e24645`), without reference to the merged #508 code.
**Delivery:** single PR, two commits, branch `impl-457-hrrr`, cross-repo PR to
`roznet/flyfun-weather:main` from the `downle` fork.

## Goal

When a route fits entirely inside the HRRR CONUS domain **and** the flight window is
within a covering run's horizon, source the `gfs` model slot's GRIB enrichment from
**HRRR** (3 km, convection-allowing, radar-assimilating) as a **full sounding
replacement** (the ICON/ECMWF pattern, not today's GFS patch). Otherwise plain GFS,
exactly as today. In-place upgrade of the existing `gfs` slot: all-or-nothing gate,
never a mixed HRRR/GFS briefing, visibly badged **GFS (HRRR)** (WRF-ARW is a
genuinely different model from FV3-GFS).

## Verified live-feed facts (2026-07-31, noaa-hrrr-bdp-pds)

- URL layout is **flat**: `hrrr.{YYYYMMDD}/conus/hrrr.t{HH:02d}z.wrfprsf{FF:02d}.grib2[.idx]`
  (no cycle subdirectory; **2-digit** fhour; `anl` step string at f00).
- idx content (00z f00, 708 messages): `CLMR` + `CIMIXR` (HRRR's ice mixing ratio
  name) on pressure levels; `DPT` direct; `REFC` + `RETOP` (echo top) present;
  `CAPE`/`CIN` at surface and `180-0 mb above ground`; `VIS`/`GUST` surface;
  `LCDC/MCDC/HCDC/TCDC`; `HGT:cloud ceiling` and `HGT:cloud base`; `LTNG` present.
- Publication is progressive (files appear one by one) → run selection probes the
  **last-needed** fhour's `.idx`, not f000.

## Architecture (approach A — standalone module + gate)

New `fetch/grib/hrrr_fetch.py` (URLs, run selection, Lambert domain gate, idx sets),
a Lambert branch in the shared interpolation helpers in `decode.py`, and an
ECMWF-shaped replacement flow selected by an all-or-nothing gate at the top of
`_enrich_gfs_inner`. Rejected: a `GfsVariant` config (over-abstraction for one
differing flow) and a 7th model slot (explicitly out of scope per issue).

### 1. Gate & run selection

- `hrrr_file_url(init_date, init_hour, fhour, idx=False)` — flat layout above.
- Cycles: hourly; **00/06/12/18z reach 48h, all others 18h**. Freshest cycle whose
  horizon covers `flight_window_end`, published (delay measured live ~1h), and with
  the last-needed fhour's idx present.
- Domain gate: build the Lambert projection from a probed wrfprs message's grid
  attributes (cfgrib exposes them; HRRR CONUS is 1799×1059 @ 3 km, LoV 262.5°E,
  LaD 38.5°N, Latin 38.5°/38.5 — read from the message, not hardcoded), project
  route points to grid x/y with pyproj, require **all** inside `[0, Nx) × [0, Ny)`.
  Exact — no lat/lon bbox approximation.
- All-or-nothing; on total HRRR failure (feed hiccup / decode error) the slot
  re-runs cleanly on plain GFS — never a half-HRRR pack (#456 fallback idiom).
- Kill switch: `WB_HRRR_ENABLED=0` disables the gate (ops escape hatch).

### 2. Lambert decode (decode.py)

- Branch in the shared interpolation helpers: a dataset with `(y, x)` dims + 2D
  lat/lon auxiliary arrays (instead of 1D lat/lon dims) → build the pyproj
  projection from the dataset's grid attributes (cached per file), transform route
  points to fractional grid indices, run the same vectorised bilinear gather on the
  `(y, x)` axes. No scipy `griddata` (slow fallback only, per issue).
- Wind rotation: HRRR wrfprs UGRD/VGRD are grid-relative when `uvRelativeToGrid=1`
  (checked from the real message). Rotate to earth-relative at decode with the
  Lambert cone-constant closed form (`α = (λ − LoV) · sin(Latin)`, verified in
  tests against pyproj-derived values).
- Name aliases: `CIMIXR` → ice mixing ratio (alongside the CLMR/CLWMR quirk);
  `DPT` used directly (no Magnus derivation); `HGT` direct (no hypsometric);
  `VVEL` already Pa/s (no −ρgw conversion).

### 3. Fetch sets + idx planning

- Sounding set: `TMP, DPT, RH, UGRD, VGRD, VVEL, HGT, CLMR, CIMIXR` on the 40
  pressure levels (25 hPa spacing) + `PRES:surface` (~190 MB/fhour envelope; thin
  to alternate levels below 500 hPa only if measurements bite).
- Diagnostics set (~15 MB/fhour): `LCDC/MCDC/HCDC/TCDC`, `HGT:cloud ceiling`,
  `HGT:cloud base`, `CAPE`/`CIN` (surface + `180-0 mb above ground`), `VIS`, `GUST`.
- Present but **not in v1**: `REFC`, `RETOP`, `LTNG` — no payload slot for them
  today; documented for a future explicit-convection track.
- `gfs_idx.py`: parse/plan helpers parametrized on variable sets; GFS defaults keep
  current behaviour. HRRR is instantaneous-only → no averaged forms, and the GFS
  averaged-window machinery (window-midpoint interp, RH/condensate gate,
  `_PREFER_AVERAGED_PAIRS`) must NOT run on HRRR data.

### 4. Enrichment

- `_enrich_gfs_inner` gates first (domain + covering run + kill switch). HRRR path
  = ECMWF-replacement-shaped: per-fhour idx → byte-range fetch → pool decode →
  `_replace_pressure_levels_from_grib` (40-level rebuild; geopotential direct from
  HGT) + cloud diagnostics (ECMWF shape: band covers + ceiling + overall base —
  no per-band geometry, no convective-scheme fields) + surface extras VIS/GUST/
  CAPE/CIN onto HourlyForecast. `cloud_area_fraction_pct` stays None (no 3-D cloud
  fraction — DD cloud/icing methods, as for GFS today).
- `_enrich_gfs` returns `(ts, source_key)` → `grib_sources["gfs"]` records
  `gfs:noaa` vs `hrrr:noaa` (the `icon_eu:dwd` / `icon_d2:dwd` precedent); the pack
  carries `model_sources["gfs"]` accordingly.
- Never mixed: the GFS patch path (CLWMR/ICMR + GFS diagnostics) runs only when
  the gate selects plain GFS.
- fill: HRRR registers with the linear sounding interp + forward-fill diag paths;
  the GFS window-midpoint diag interp does not see HRRR-sourced hours
  (`gfs_init=None` for the slot when HRRR).

### 5. Wiring

- `cache.py`: `hrrr` cache dir, 6h TTL (hourly cycles).
- `fetch/freshness/registry.py`: `hrrr:noaa` SourceConfig — cycles (0, 6, 12, 18),
  48h horizon, delivery offset measured live (~1h), readiness check, description
  documents the hourly-cycle layout and the gfs-slot upgrade role.
- Badge: **GFS (HRRR)** in the freshness bar + popover, mirroring the
  "ICON (D2)" mechanics.
- `pyproject.toml`: pyproj promoted from transitive to declared dependency
  (precedent for its use: `fetch/chart_cache.py`, `fetch/metoffice_calibrate.py`).

### 6. Tests

- Unit: URL builders (flat layout, 2-digit fhour); run selection across 18h/48h
  cycles incl. publish-delay + last-needed probing; domain gate via projection
  (inside / outside / edge); idx planning with `CIMIXR` + diagnostics sets
  (synthetic idx text); wind-rotation closed form; diagnostics builder (ECMWF
  shape, CIN negative convention); gate fallback (total failure → plain GFS);
  fill semantics (no GFS averaged-window machinery on HRRR hours); registry/cache
  wiring; kill switch.
- Golden decode: real byte-ranged wrfprs sample under `tests/data/hrrr_samples/`
  (gitignored, skip-if-absent — the ECMWF sample-test precedent): projected-grid
  interpolation sub-kelvin against a nearest-neighbour readback; wind rotation
  against the cone-constant closed form.
- Integration shape: KBOS→KBWI at D-1 → HRRR (badged); same route at D-5 → GFS;
  route leaving CONUS → GFS.

### 7. Docs

- `designs/weather-engine-specs.md`: HRRR section + field-attribution matrix update.
- `designs/fetch.md`: HRRR fetch section.

## Error handling / honesty rules (inherited house rules)

- `None ≠ 0`: failed fetches/decodes are unknown, never quiet; a total HRRR failure
  falls back to plain GFS whole, never a half-HRRR pack.
- All HRRR fields instantaneous — plain forward-fill / linear time interp; the GFS
  averaged-window machinery stays GFS-only.
- Badge is mandatory, not cosmetic: WRF-ARW ≠ FV3, so the pack must say which model
  actually sourced the slot.

## Out of scope (per issue)

HRRR-Alaska; sub-hourly output; HRRR ensemble; RRFS migration (URL config kept
parametrized so a bucket/path swap is contained); REFC/RETOP/LTNG payload fields;
Open-Meteo HRRR surface-sourcing changes (verify `gfs_seamless` blend note only).
