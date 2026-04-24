# IFS Cycle 50r1 Migration Plan

> Adapt our ECMWF GRIB ingestion pipeline for IFS Cycle 50r1 changes.

**Status:** Implemented in PR #89 (2026-04-24). Cycle goes live 12-May-2026.
This document is retained as the historical plan; current authoritative
reference is `weather-engine-specs.md` and `analysis-metrics.md`.

Notable deltas from the plan below, discovered during implementation:
- Horizon is now derived from the max step observed on disk per run,
  not from parsed init-hour or stream name (see `find_best_ecmwf_run`).
- 00/12z horizon is 168h post-2026-04-22 amendment, not 192h.
- `delivery_config.json` was re-keyed by init hour (0/6/12/18) instead
  of stream name — cleaner and future-proof for similar rename events.
- TPREd test feed was mis-provisioned by ECMWF on first delivery; they
  are re-sending with the correct spec.

**Reference:** https://confluence.ecmwf.int/display/FCST/Implementation+of+IFS+Cycle+50r1
**Test data:** expver `0080`, available at https://data.ecmwf.int/forecasts/testdata/

## What's Changing

IFS Cycle 50r1 merges HRES into the ENS control forecast and restructures
stream/type identifiers. No changes to resolution (TCO1279, 137 levels, ~9km)
or the parameters we currently consume.

### Stream/Type Renaming (Breaking)

| Run    | Before (49r2)              | After (50r1)              |
|--------|----------------------------|---------------------------|
| 00/12z | `stream=enfo, type=cf`     | `stream=oper, type=fc`    |
| 06/18z | `stream=scda`              | `stream=oper`             |
| Wave   | `stream=waef, type=cf`     | `stream=wave, type=fc`    |

Our `ecmwf_fetch.py` filename parser filters on stream name. The `scda` →
`oper` consolidation means 06/18z files will no longer be distinguishable by
stream — we'll need to differentiate by init hour instead.

### GRIB Encoding

- Atmospheric model ID: 158 → 161 (GRIB1 Section 1)
- Master tables version: 32 → 35 (GRIB2)
- Our cfgrib decoder uses shortName/paramId so likely unaffected, but must verify.

### ECPDS Subscription

Our order is for IFS-ENS-CF. With ENS control merging into the operational
stream, we need to confirm with ECMWF that our delivery continues under the
new naming.

## Required Changes

### 1. Validate GRIB Decoding with Test Data

Download 50r1 test data (expver `0080`) and run it through our pipeline:
- `decode.py` — cfgrib opens and maps variables correctly
- `ecmwf_fetch.py` — filename parser handles new stream/type values
- Check that `clwc`, `ciwc`, `cc`, `ceil`, `cbh`, cloud cover fields all present

### 2. Update `ecmwf_fetch.py` Stream Handling

Current logic distinguishes `oper` (00/12z, 192h) from `scda` (06/18z, 144h).
Post-50r1 both arrive as `stream=oper`. Options:
- Parse init hour from filename timestamp to determine forecast horizon
- Use file metadata (step range) instead of stream name

### 3. Check Step Cadence & Update `delivery_config.json`

Our current subscription (IFS-ENS-CF) delivers **3-hourly throughout** (0, 3,
6, …, 144/192). The HRES/Set I schedule uses hourly 0–90, then 3-hourly
93–144, then 6-hourly 150–360. With HRES merging into the ENS control, the
post-50r1 product might switch to hourly cadence for the first 90 steps.

**Check with test data (expver 0080):**
- List the delivered steps — still 3-hourly, or now hourly 0–90?
- If hourly: update `delivery_config.json` step list (57 → ~109 steps for
  oper, 49 → ~109 for scda/oper-06/18z). File count per run doubles.
- The sentinel watcher is count-based (`actual >= expected`). With hourly
  steps, delivery arrives in two batches (~15 min gap between 0–90 and
  93–144). This is well within the 2h `completeness_timeout_hours` so no
  logic change needed — just the correct step list in the config.

**Current production config** (`/mnt/flyfun_data/ecmwf/data/delivery_config.json`):
- `oper`: 57 steps × 2 parts = 114 files
- `scda`: 49 steps × 2 parts = 98 files

### 4. Confirm ECPDS Subscription

Contact ECMWF to confirm:
- Our delivery will continue under the new stream/type naming
- Whether the filename format changes (model identifier field, etc.)

## Opportunities

- **Wet-bulb temperature** — new parameter, useful for icing (wet-bulb zero
  height). Add to `ECMWF_SOUNDING_VARS` if available in delivery.

## No Impact Areas

- Open-Meteo path: changes absorbed transparently by Open-Meteo
- Parameters we use: all continue unchanged
- Resolution/levels: unchanged
- Meteorological quality: small improvements to cloud cover, dewpoint, 10m
  winds (1-2%) — free upside
