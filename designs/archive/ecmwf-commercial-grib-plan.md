# ECMWF Commercial GRIB Integration Plan

> Integrate ECMWF IFS real-time GRIB data (commercial feed via ECPDS) to enrich
> the ECMWF cross-section with cloud microphysics and eventually full sounding data.

**Status:** FULLY IMPLEMENTED & LIVE IN PRODUCTION (since 2026-04-20). This
plan is now historical — all phases (0 sample validation → 1 fetch → 2 pipeline
→ 3 full sounding) shipped. The durable design home is `designs/fetch.md`
(see its "ECMWF IFS enrichment" section + gotchas); the IFS Cycle 50r1 follow-on
is documented in `designs/./ifs-cycle-50r1-migration.md`. The real
subscription delivers **ifs-ens-cf** at 0.25° (a1 = 29 surface vars, a2 = 10
vars × 25 pressure levels), not the speculative HRES/AIFS choices weighed below.
Code lives in `ecmwf_fetch.py` + `ecmwf_watcher.py` + `_enrich_ecmwf()` in
`fetch/grib/__init__.py`. Kept only as a record of the original validation plan;
recommend archiving.

## Background

We currently get ECMWF data through Open-Meteo's free API, which provides
standard meteorological fields but **lacks cloud liquid water (CLWMR) and ice
mixing ratio (ICMR)** — the variables we need for accurate icing assessment.

ECMWF offers a commercial real-time data feed delivered via ECPDS. We have
received sample GRIB files to validate our decode pipeline before signing the
service agreement.

### File Naming Convention (from ECMWF)

```
xxx_cc_nnn_cl_ssss_t_YYYYMMDDTHHMMSSZ_YYYYMMDDTHHMMSSZ_h

xxx:    Destination name (ECPDS)
cc:     Feed name (PREd)
nnn:    Model identifier (e.g. aifs-ens, ifs)
cl:     Data class (od = operational)
ssss:   Stream name (e.g. oper, enfo)
t:      Type (e.g. fc = forecast)
YYYYMMDDTHHMMSSZ:  Base date/time (forecast init)
YYYYMMDDTHHMMSSZ:  Valid date/time
h:      Forecast step (hours or days)
```

Reference: [ECMWF file naming convention](https://confluence.ecmwf.int/display/DAC/File+naming+convention+and+format+for+real-time+data)

### Key Differences from GFS/ICON-EU

| Aspect | GFS | ICON-EU | ECMWF Commercial |
|--------|-----|---------|-----------------|
| Access | Public S3, no auth | Public DWD, no auth | ECPDS delivery, authenticated |
| Index files | `.idx` companion files | N/A (individual files) | None — full GRIB files |
| Grid | 0.25° regular lat-lon | ~6.5km regular lat-lon | 0.1° or 0.25° (TBD from sample) |
| Compression | None (byte-range) | bz2 per file | Likely uncompressed GRIB2 |
| Delivery | Pull from S3 | Pull from DWD | Push to our server (ECPDS) or pull |
| Decode | cfgrib + byte offset | cfgrib per file | cfgrib or ecCodes |
| Variables | Per-message in single file | Per-file | TBD — likely multi-message per file |

## Phase 0: Validate Sample Files (DONE — Apr 2026)

### Goal
Confirm we can read the test GRIB files, extract the variables we need, and
understand the file structure before committing to the service agreement.

### Tasks

#### 0.1 — Inventory the sample files
- List all received files, parse the naming convention
- Identify which model (IFS HRES? AIFS-ENS?), class, stream, type
- Note the base times, valid times, and forecast steps
- Determine if files are single-message or multi-message GRIB2

#### 0.2 — Decode with cfgrib and ecCodes
Write `tests/test_ecmwf_sample.py`:

```python
"""Validate ECMWF sample GRIB files can be decoded."""
import cfgrib
import xarray as xr
from pathlib import Path

SAMPLE_DIR = Path("tests/data/ecmwf_samples")

def test_list_messages():
    """Inventory all GRIB messages in sample files."""
    for grib_file in sorted(SAMPLE_DIR.glob("*.grib2")):
        datasets = cfgrib.open_datasets(str(grib_file))
        for i, ds in enumerate(datasets):
            print(f"\n{grib_file.name} dataset[{i}]:")
            print(f"  Variables: {list(ds.data_vars)}")
            print(f"  Coords: {dict(ds.coords)}")
            print(f"  Dims: {dict(ds.dims)}")

def test_extract_cloud_water():
    """Verify cloud liquid water / ice fields are present."""
    for grib_file in sorted(SAMPLE_DIR.glob("*.grib2")):
        datasets = cfgrib.open_datasets(str(grib_file))
        for ds in datasets:
            vars = set(ds.data_vars)
            # Check for cloud microphysics (names may vary)
            clw_candidates = {"clwc", "clwmr", "clmr", "qliq", "q_liquid"}
            ice_candidates = {"ciwc", "icmr", "qice", "q_ice"}
            found_clw = vars & clw_candidates
            found_ice = vars & ice_candidates
            if found_clw or found_ice:
                print(f"Found microphysics: CLW={found_clw}, ICE={found_ice}")

def test_spatial_interpolation():
    """Verify we can interpolate to a route point."""
    # Use a known European coordinate
    test_lat, test_lon = 48.0, 11.5  # near Munich
    for grib_file in sorted(SAMPLE_DIR.glob("*.grib2")):
        datasets = cfgrib.open_datasets(str(grib_file))
        for ds in datasets:
            if "latitude" in ds.coords and "longitude" in ds.coords:
                point = ds.sel(latitude=test_lat, longitude=test_lon,
                              method="nearest")
                print(f"Interpolated values at ({test_lat}, {test_lon}):")
                for var in ds.data_vars:
                    print(f"  {var}: {point[var].values}")
```

#### 0.3 — Document findings
After running tests, update this file with:
- Actual variable names (ECMWF shortNames)
- Grid resolution and domain
- Pressure levels or model levels present
- File sizes and multi-message structure
- Any ecCodes-specific quirks (ECMWF recommends ecCodes for decoding)

#### 0.4 — ecCodes fallback
If cfgrib cannot handle the files cleanly, test with ecCodes directly:
```python
import eccodes
# Low-level message iteration for tricky GRIB2
```
ECMWF specifically recommends ecCodes. If cfgrib works, prefer it for
consistency with GFS/ICON-EU paths. If not, use ecCodes and wrap in our
decode interface.

### Decision gate
Report findings back to ECMWF, confirm files look OK, sign the service
agreement (or request different variables/levels).

## Phase 1: ECMWF GRIB Fetch Module

### 1.1 — New module: `src/weatherbrief/fetch/grib/ecmwf_fetch.py`

Follow the pattern of `icon_eu_fetch.py` (separate fetch module per model):

```python
"""ECMWF IFS GRIB2 fetch from ECPDS delivery directory.

ECMWF commercial data is delivered to a local directory via ECPDS.
Unlike GFS (S3 byte-range) and ICON-EU (DWD HTTP), files land on disk
directly — no HTTP download step.
"""

ECMWF_DATA_DIR = Path("/data/ecmwf")  # configurable via env var

# Variables we want from ECMWF IFS
ECMWF_VARIABLES = {
    "clwc": "cloud_liquid_water_kg_kg",  # Cloud liquid water content
    "ciwc": "ice_mixing_ratio_kg_kg",    # Cloud ice water content
    # Phase 3 additions:
    # "t": "temperature_k",
    # "q": "specific_humidity_kg_kg",
    # "u": "u_wind_ms",
    # "v": "v_wind_ms",
}
```

Key design decisions:
- **Local file access vs pull**: ECPDS pushes files to our server. No HTTP
  download needed — just read from the delivery directory. This is simpler
  than GFS/ICON-EU but we need a file-watcher or directory scanner.
- **File discovery**: Parse filenames using the naming convention to find
  files matching the target init time and forecast hour.
- **Run selection**: Like `find_latest_run()` for GFS — scan the delivery
  directory for the most recent IFS run whose files are complete.

### 1.2 — File discovery helper

```python
@dataclass
class ECMWFFileInfo:
    """Parsed ECMWF filename metadata."""
    path: Path
    model: str          # "ifs", "aifs-ens", etc.
    data_class: str     # "od" for operational
    stream: str         # "oper", "enfo"
    data_type: str      # "fc" for forecast
    base_time: datetime # forecast init
    valid_time: datetime
    step_hours: int

def scan_ecmwf_files(
    data_dir: Path,
    model: str = "ifs",
    base_time: datetime | None = None,
) -> list[ECMWFFileInfo]:
    """Scan ECPDS delivery directory for available ECMWF files."""
```

### 1.3 — Decode integration

Add ECMWF variable mapping to `decode.py`:
```python
_VAR_MAP.update({
    "clwc": "cloud_liquid_water_kg_kg",  # ECMWF cloud liquid water content
    "ciwc": "ice_mixing_ratio_kg_kg",    # ECMWF cloud ice water content
})
```

If ECMWF files are multi-message (all levels in one file), we can use
cfgrib's `open_datasets()` directly. If files contain a mix of surface and
pressure-level data, we may need ecCodes to filter messages.

## Phase 2: Pipeline Integration

### 2.1 — Add ECMWF to `enrich_forecasts()`

In `src/weatherbrief/fetch/grib/__init__.py`, extend the existing parallel
enrichment pattern:

```python
def enrich_forecasts(...):
    # Existing: GFS + ICON-EU in parallel
    # New: Add ECMWF as a third source

    with ThreadPoolExecutor(max_workers=3) as pool:
        gfs_future = pool.submit(_enrich_gfs, ...)
        icon_dl_future = pool.submit(_prefetch_icon_eu_data, ...) if icon_ctx else None
        ecmwf_future = pool.submit(_enrich_ecmwf, ...)  # NEW

        gfs_ts = gfs_future.result()
        ecmwf_ts = ecmwf_future.result()  # NEW
        if icon_dl_future:
            icon_dl_future.result()

    # ICON-EU decode (sequential, memory-heavy)
    ...

    # Record init times
    if ecmwf_ts is not None:
        grib_init_times["ecmwf"] = ecmwf_ts
```

### 2.2 — `_enrich_ecmwf()` function

```python
def _enrich_ecmwf(
    cross_sections: list[RouteCrossSection],
    route_points: list[RoutePoint],
    departure_time: datetime,
    *,
    data_dir: Path,
    flight_duration_hours: float = 0.0,
) -> int | None:
    """Enrich ECMWF cross-sections with CLWC/CIWC from commercial GRIB data."""
    ecmwf_sections = [cs for cs in cross_sections if cs.model == ModelSource.ECMWF]
    if not ecmwf_sections:
        return None

    # Find latest available ECMWF run
    ecmwf_files = scan_ecmwf_files(ECMWF_DATA_DIR)
    if not ecmwf_files:
        logger.info("No ECMWF GRIB files available, skipping enrichment")
        return None

    # Decode and interpolate to route points
    # Merge into pressure-level data
    ...
```

### 2.3 — Gap-filling

Follow established pattern:
1. **Time axis**: Add ECMWF forward-fill in `fill.py` → `propagate_all()`
2. **Spatial axis**: Reuse existing spatial interpolation (it's model-agnostic)
3. **Vertical axis**: If ECMWF data is on model levels (like ICON-EU), add
   log-pressure interpolation. If on pressure levels, simpler merge.

### 2.4 — Cloud diagnostics

ECMWF IFS produces cloud diagnostic fields (cloud base, ceiling, layer covers).
If present in the commercial feed:
- Decode into `NWPCloudDiagnostics` (same model as GFS/ICON-EU)
- Apply via `_apply_cloud_diagnostics()` for ECMWF cross-sections
- Currently ECMWF cross-sections have no NWP cloud diagnostics — this would
  be a significant improvement

## Phase 3: Full Sounding (Future)

Following the ICON-EU full sounding plan (`icon-full-grib-plan.md`), once the
basic microphysics enrichment works, extend to full sounding data:

- T, Q, U, V from ECMWF GRIB → replace Open-Meteo pressure levels
- Same derived quantity computation (RH, dewpoint, wind speed/direction)
- Higher vertical resolution than Open-Meteo's 11 ECMWF pressure levels
- Shared `build_pressure_levels()` abstraction for both ICON-EU and ECMWF

This is the same vision described in `icon-full-grib-plan.md` §9 ("ECMWF IFS
commercial API access is pending — if granted, the same framework should handle
both models").

## Data Delivery Architecture

ECPDS pushes files to our server. We mount that directory into the Docker
container as a read-only volume and scan it from the pipeline.

### Configuration (IMPLEMENTED)

**`.env` / `.env.sample`:**
```bash
# Directory where ECMWF files are delivered (mounted as Docker volume in prod)
ECMWF_GRIB_DIR=/data/ecmwf
# Host path for Docker volume mount
HOST_ECMWF_GRIB_DIR=/path/to/ecmwf/data
```

**`docker-compose.yml`:**
```yaml
volumes:
  - ${HOST_DATA_DIR:-./data}:/app/data
  - ${HOST_ECMWF_GRIB_DIR:-./data/ecmwf}:/data/ecmwf:ro  # ECMWF GRIB (read-only)
```

**In code** (`ecmwf_fetch.py`):
```python
def ecmwf_grib_dir() -> Path:
    return Path(os.environ.get("ECMWF_GRIB_DIR", "/data/ecmwf"))
```

For **local dev/test**: set `ECMWF_GRIB_DIR` in your `.env` to wherever
the sample files live, or symlink `tests/data/ecmwf_samples/` and run
tests with `ECMWF_GRIB_DIR=tests/data/ecmwf_samples`.

The volume is `:ro` (read-only) since ECPDS manages file delivery and
our code only reads. Retention/cleanup of old files is handled on the
host side (ECPDS or a cron job), not by our application.

## Testing Strategy

### Unit Tests (`tests/test_ecmwf_grib.py`)

1. **Filename parser** — verify `ECMWFFileInfo` parsing against all naming
   variants (operational, experimental, different models)
2. **Variable extraction** — decode sample file, verify CLWC/CIWC values
   are physically reasonable (0–1e-3 kg/kg range)
3. **Spatial interpolation** — extract values at known lat/lon, verify
   against eyeballed expected values from the sample
4. **Run discovery** — given a set of files, `scan_ecmwf_files()` finds the
   correct latest run

### Integration Tests

5. **Pipeline round-trip** — create a test route, place sample GRIB files,
   run `enrich_forecasts()`, verify ECMWF sections get CLWMR/ICMR populated
6. **Graceful degradation** — empty directory → ECMWF enrichment skipped,
   GFS/ICON-EU unaffected
7. **Mixed availability** — ECMWF files for some hours but not others →
   gap-filling works correctly

### Regression

8. **GFS/ICON-EU unchanged** — existing test_grib.py and test_grib_fill.py
   still pass (ECMWF addition is purely additive)

## File Layout

```
src/weatherbrief/fetch/grib/
├── __init__.py           # Phase 2: add _enrich_ecmwf, extend enrich_forecasts
├── ecmwf_fetch.py        # DONE: filename parser, directory scanner, run discovery
├── decode.py             # DONE: ECMWF variable mappings (clwc, ciwc)
├── fill.py               # Phase 2: add ECMWF forward-fill
├── cache.py              # N/A (ECMWF files are already on disk, no caching needed)
├── gfs_idx.py            # Unchanged
├── grib_fetch.py         # Unchanged (GFS-specific)
├── icon_eu_fetch.py      # Unchanged
└── icon_eu_levels.py     # Unchanged

tests/
├── data/
│   └── ecmwf_samples/    # DONE: gitignored, set ECMWF_GRIB_DIR to point here
├── test_ecmwf_sample.py  # DONE: Phase 0 validation (parser + scanner + GRIB decode)
└── test_ecmwf_grib.py    # Phase 1-2: unit + integration tests

Config:
├── .env.sample           # DONE: ECMWF_GRIB_DIR, HOST_ECMWF_GRIB_DIR
├── docker-compose.yml    # DONE: read-only volume mount for ECMWF data
└── .gitignore            # DONE: excludes tests/data/ecmwf_samples/
```

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| cfgrib can't decode ECMWF files | Fall back to ecCodes (ECMWF's own library); wrap in same interface |
| Variable names differ from expected | Phase 0 inventories actual shortNames before coding |
| Data on model levels (not pressure) | Reuse ICON-EU log-pressure interpolation approach |
| ECPDS delivery delays | Graceful skip; GFS/ICON-EU continue independently |
| Disk space from accumulating files | Retention cleanup (configurable hours, cron or in-pipeline) |
| Large file sizes | ECMWF files are already local; no download bottleneck |
| Service agreement terms change | Keep feature-flagged so we can disable without code changes |

## Open Questions (To Resolve in Phase 0)

1. **What variables are in the sample files?** — Need to inventory actual
   shortNames, levels, and grid resolution
2. **Model levels or pressure levels?** — Determines whether we need vertical
   interpolation (like ICON-EU) or can merge directly
3. **Single file per step or split by variable?** — Determines decode strategy
4. **IFS HRES or AIFS-ENS?** — Different model, different characteristics
5. **Cloud diagnostic fields included?** — Do we get cloud base/ceiling/covers?
6. **Delivery frequency** — How soon after init time do files arrive?
7. **File sizes** — Determines disk space planning for retention

## Implementation Order

```
Phase 0  ──►  Sign agreement  ──►  Phase 1  ──►  Phase 2  ──►  Phase 3
(done)        (done)                (done)        (done)        (done)
```

All phases completed by 2026-04-20. The answers to the open questions above
are recorded in `ecmwf_fetch.py`'s module docstring (ifs-ens-cf, 0.25°,
a1 = 29 surface vars, a2 = 10 vars × 25 pressure levels, ~6-8h publication
delay) and the durable design is in `designs/fetch.md`.
