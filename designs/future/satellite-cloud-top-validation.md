# Satellite Cloud-Top Validation for GA Routes

> **2026-09-05 correction:** this is a historical investigation, not the
> current decoder specification. Observed conditions have since shipped.
> The original method-code interpretations and inferred multilayer/sky-area
> conclusions were incorrect or unsupported; corrected below against the
> [FCI product guide](https://user.eumetsat.int/resources/user-guides/mtg-fci-clm-ct-and-ctth-data-guide).
> Large low-cloud parallax offsets and effective-cloudiness packing still need
> independent real-granule validation. See the
> [full PR #584 review](../reviews/2026-09-05-pr584-observed-review.md).

**Status: Investigation paused 2026-05-10** (still paused, re-checked
2026-08-15) — MTG L2 CTTH (Cloud Top Temperature & Height) was successfully
fetched and sampled; OCA collection is accessible but not yet exercised; L1
brightness-temperature (what Windy displays) is gated behind a separate
EUMETSAT licence not yet on our account. Nothing from this investigation has
been wired into `src/weatherbrief/` — it lives entirely in the four `scripts/`
CLIs below.

Goal: cross-check the cloud picture along a GA route against what a pilot
actually flew through, using EUMETSAT geostationary satellite products.

## Why this matters

Pilots compare their in-flight cloud observations against forecasts (Windy,
GRAMET) and want a way to ground-truth those forecasts post-flight. The
satellite "saw" the same scene from above — if we can sample its cloud-top
products at each route waypoint over the flight window, we can validate (or
correct) the forecast picture.

This was triggered by the 2026-05-04 flight LSGS → LSGL → LFQB → LFQA → BILGO
→ LFAT → EGTF, where the pilot flew through a slow-moving warm front with
multi-layer cloud (high cirrus + mid layer + low stratus) and Windy's satellite
imagery was the only thing showing a "consistent" cloud picture along the
route. CTTH alone proved insufficient; this doc captures why and what to try
next.

## Datasets explored

EUMETSAT Data Store collections, accessed via `eumdac` Python client and OAuth
credentials in `.env` (`EUMETSAT_CONSUMER_KEY` / `EUMETSAT_CONSUMER_SECRET`).

| Collection | Title | Status | Notes |
|---|---|---|---|
| `EO:EUM:DAT:0681` | Cloud Top Temp & Height — MTG | ✅ accessible | What we used |
| `EO:EUM:DAT:0684` | Optimal Cloud Analysis (OCA) — MTG | ✅ accessible | Not yet fetched. Provides upper + lower layer per pixel |
| `EO:EUM:DAT:0662` | FCI L1c Normal Resolution Image Data — MTG | ❌ 403 Forbidden | The IR brightness temperature data Windy displays |
| `EO:EUM:DAT:0665` | FCI L1c High Resolution Image Data — MTG | not tested | Likely also gated |
| `EO:EUM:DAT:MSG:HRSEVIRI` | High Rate SEVIRI L1.5 — MSG | ❌ 403 Forbidden | MSG predecessor of L1c |
| `EO:EUM:DAT:MSG:MSG15-RSS` | Rapid Scan SEVIRI L1.5 — MSG | ❌ 403 Forbidden | Rapid-scan over Europe (5-min cadence) |
| `EO:EUM:DAT:MSG:CTH` | Cloud Top Height — MSG | ✅ accessible | Native binary format (.nat); needs satpy |
| `EO:EUM:DAT:MSG:CLM` | Cloud Mask — MSG | ✅ accessible | Same |
| `EO:EUM:DAT:MSG:OCA` | (Optimal Cloud Analysis — MSG) | ❌ 404 (probably retired or moved) |  |

The L1.5 / L1c access gate is a separate "EUMETSAT Data Centre / EUMETCast
Terrestrial" registration on user.eumetsat.int, not the API key. Click-through
licence; usually granted in minutes for non-commercial use. **Action needed
to unblock the most useful dataset.**

## What we built

All scripts in `scripts/`. They share a small idiom: read OAuth creds from
`.env`, look up airport coords from `data/nav.db`, project (lon, lat) onto
the MTG geostationary frame, sample cached netCDFs.

### `scripts/fetch_mtg_ctth_route.py`

End-to-end CLI: searches CTTH products in a time window, downloads zips to
`/tmp/eumetsat_ctth/cache/`, opens the inner netCDF, samples
`cloud_top_temperature` / `cloud_top_height` / `cloud_top_pressure` at each
named airport plus optional great-circle midpoints. Prints a per-timestep
table and optional CSV.

```bash
python scripts/fetch_mtg_ctth_route.py \
    --start 2026-05-04T08:00 --end 2026-05-04T10:00 \
    --airports LSGL LFQB LFQA LFAT EGTF \
    --csv /tmp/route.csv
```

### `scripts/analyze_mtg_ctth_route.py`

Reads cached zips, no network. For each waypoint × timestep, prints THREE
samples side-by-side so the limitation of single-pixel sampling is visible:

1. **nominal** — nearest pixel to the airport's surface footprint.
2. **parallax** — pixel whose corrected ground location (pixel_lat + dlat,
   pixel_lon + dlon) is nearest the airport.
3. **highest** — the script selects coldest CTT in the search box. This is a
   heuristic, not necessarily the highest top (e.g. temperature inversions).

Useful diagnostic; not the final answer.

### `scripts/print_ctth_route_table.py`

Loads the named waypoints from a debug pack's `route_points.json`, prints
two pivot tables (waypoint × timestep): "highest cloud top in window" vs
"parallax-corrected at airfield". Tightest representation of "what L2
returned for each waypoint over time".

### `scripts/ctth_route_histogram.py`

The most useful view. For each waypoint × timestep, takes a 10 km radius
circle around the *parallax-corrected* ground projection of each cloud-top
pixel. Histograms the geometric cloud tops in five height bands and breaks
down retrieval methods. Legacy "FL" values here mean hundreds of geometric
feet MSL, not pressure flight levels. Several modes describe neighbouring
retrievals; they cannot establish stacked layers in one column.

```bash
python scripts/ctth_route_histogram.py --radius-km 10
```

## Key technical findings

### CTTH file structure (collection EO:EUM:DAT:0681)

- One netCDF per 10-min repeat cycle, ~95 MB each, packaged in a zip with
  quicklook PNGs and EOPMetadata.xml.
- 5568 × 5568 grid in MTG geostationary projection (lon0=0°, h=35786400 m,
  sweep=y, ellipsoid a=6378137 / b=6356752).
- Variables of interest:
  - `cloud_top_temperature` (K, float32) — for opaque clouds ≈ IR window BT
  - `cloud_top_height` (m, float32)
  - `cloud_top_pressure` (hPa, float32)
  - `cloud_top_aviation_height` (FL/10, float32)
  - `effective_cloudiness` (decoded 0–1 in this investigation despite percent
    metadata; verify packing on current granules before any normalization).
    Cloud amount × 10.5 µm emissivity, not visual opacity or METAR cloud amount.
  - `delta_latitude` / `delta_longitude` (degrees, int8 ×0.01) — per-pixel
    parallax shift; corrected ground location = pixel_lat + dlat
  - `quality_method` (int8) — height assignment method, see codes below

### Geolocation

```python
from pyproj import CRS, Transformer
geos = CRS.from_proj4(
    "+proj=geos +lon_0=0 +h=35786400 +a=6378137 +b=6356752 "
    "+sweep=y +units=m +no_defs"
)
fwd = Transformer.from_crs(CRS.from_epsg(4326), geos, always_xy=True)
x_m, y_m = fwd.transform(lon, lat)
# netCDF stores x,y as scan-angle radians, divide projected metres by h:
x_rad, y_rad = x_m / 35786400, y_m / 35786400
col = int(np.argmin(np.abs(ds.x.values - x_rad)))
row = int(np.argmin(np.abs(ds.y.values - y_rad)))
```

Round-trip pixel→lat/lon recovers within ~0.01–0.02° (one ~2 km MTG pixel).
Sub-satellite point lands exactly at row/col 2784 (= 5568/2).

### Parallax direction (this caught me out — note for next time)

In this projection, **larger row index = larger latitude** (north). The
satellite is at the equator, so for a cloud over an airfield at 50°N, the
satellite's line-of-sight extends past the cloud and intersects ground
*north* of the cloud's true position. Therefore:

- The pixel containing the cloud-top has its ground intersection NORTH of
  the cloud's true location.
- `delta_latitude` is **negative** (corrected_lat = pixel_lat + dlat puts
  the cloud south of the pixel).
- To find the cloud over an airfield at lat=L, search pixels with
  pixel_lat > L (NORTH of the airfield), i.e., **larger row indices**.

Reported magnitude: dlat ≈ −0.4° even for low cloud (0–5000 geometric ft) at
50°N, growing to ≈ −0.6° for high cirrus. These unexpectedly large low-cloud
values disagree with a simple viewing-geometry estimate and are **unresolved**.
Recheck packed units, grid, signs and independent positions on real granules;
neither the synthetic fixture nor this discrepancy validates a new scale factor.

### `quality_method` codes (FCI L2 CTTH)

The original pixel shares/temperatures below are historical measurements, not
evidence of enum meaning. Meanings are corrected from FCI Guide Table 10.

| Code | Historical pixels | Historical median CTH / CTT | Guide meaning |
|---|---|---|---|
| 0 | 62% | — | Unprocessed: no/corrupt data **or** cloud-free |
| 1 | 16% | 1261 m / +11 °C | Opaque and RTM |
| 2 | — | — | Opaque minus RTM |
| 3 | — | — | Intercept IR10.5/IR13.4 |
| 4 | — | — | Intercept IR10.5/IR6.3 |
| 5 | — | — | Intercept IR10.5/IR7.3 |
| 6 | 13% | 10377 m / −45 °C | Radiance ratio IR10.5/IR13.4 |
| 7 | <1% | 12962 m / −59 °C | Radiance ratio IR10.5/IR6.3 |
| 8 | 3% | 10546 m / −46 °C | Radiance ratio IR10.5/IR7.3 |
| 9 | 4% | 1236 m / +2 °C | Opaque + RTM + inversion; **not multilayer** |
| 10 | 1.5% | — | No solution |

Use separate status flags to distinguish cloud-free (1), failed cloud retrieval
(2), successful cloud retrieval (3), unprocessed (0), and dust/ash (4–7).
Processing quality also matters; method 0 alone cannot establish clear sky.

### CTTH's fundamental limitation for multi-layer scenes

CTTH commits to **one cloud-top per pixel**. Semitransparent cloud over another
layer can make interpretation difficult and nearby pixels may return different
heights. A spatial histogram exposes that variability, but does not recover
hidden layers or prove a vertical stack. The original optical-depth thresholds
were not validated for this product, and method 9 is not evidence of multilayer.

## What we found for the 2026-05-04 flight

Pilot's report:
- LSGL: high cloud −36 °C
- LSGL → LFQB: mid layer −15 °C / FL160+
- LFQA: very high layer −42 °C, "moving so timing may be an issue"
- North of France: lower layer −5 to −10 °C
- EGTF: lower layer +4 to +6 °C

CTTH-histogram (10 km parallax-corrected radius, FCI L2) verdict:

> Historical interpretations below are hypotheses, not validated layer/sky-area
> measurements. The old status decoding and sample denominator require reanalysis;
> correspondence with a pilot's report is useful context, not proof of retrieval
> correctness. Descriptions of "FL" from geometric CTH use a legacy mislabel.

- **LSGL**: low stratus 50–60% + 8–14% mid + 1–5% high cirrus across all
  timesteps. ✅ All three layers present, matches pilot.
- **LFQB**: 100% low at 10 km radius. The mid layer was *between waypoints*,
  not over LFQB itself; a wider 70 km box caught it (visible in pilot's
  side window in flight, not directly overhead).
- **LFQA / BILGO**: 100% low at 10 km. Pilot's −42 °C cirrus was
  transient/patchy, not in any single 10-min snapshot directly overhead.
- **LFAT**: 67–100% cloudy, 100% low at 10 km. **Pilot's mid layer at
  −5 to −10 °C was not detected by L2 CTTH.** Likely thin altostratus that
  the L2 algorithm declared "no cloud top here" because optical depth was
  below threshold.
- **EGTF**: 100% cloudy, all classified as low stratus, with the top rising
  from FL040 to FL060–80 through the morning. ✅ Pilot's +4 to +6 °C matches
  early window.

## Why Windy's satellite layer "looked right" while CTTH didn't

Crucial clarification from the pilot: they were comparing against Windy's
**satellite imagery layer**, not Windy's model layer.

Windy's satellite layer renders **L1 IR window brightness temperature**
(typically ~10.8 µm) directly as false colour, no L2 retrieval. Continuous
tone, no pixel commitment to a single layer. For the same multi-layer
scene:

- Opaque cirrus → cold pixel
- Opaque stratus → warm pixel
- **Thin cirrus over warm stratus → BT is a radiative MIX of the two**, e.g.
  −15 °C, which the eye reads as "thin cirrus there"

CTTH throws this gradient away by labelling each pixel discretely. That's
why thin As over LFAT (which shows up in raw IR BT as a coolish pixel)
becomes "low stratus, +6 °C" in CTTH.

For matching what Windy displays, **L1 IR BT is the right product**, not
CTTH or OCA.

## Open questions / next steps

1. **Get L1 BT access** — register on user.eumetsat.int for "FCI L1c Normal
   Resolution" (collection `EO:EUM:DAT:0662`) or "High Rate SEVIRI L1.5"
   (`EO:EUM:DAT:MSG:HRSEVIRI`). Once granted, the `.nat` files for SEVIRI
   need `satpy` to read (already installed in venv); FCI L1c is netCDF
   chunked (~40 chunks per 10-min cycle, only chunks ≥0030 cover Europe).

2. **Try OCA** (`EO:EUM:DAT:0684`) as the structural fix for multi-layer.
   Variables expected: upper-layer cloud-top pressure / optical depth /
   effective radius / phase, same set for lower layer, plus scene-type
   flag. Same projection as CTTH so the existing geolocation + 10 km
   parallax-corrected histogram code reuses directly.

3. **Compare OCA's lower layer top against CTTH's CTT for the LFAT / EGTF
   case** — does OCA recover the −5 to −10 °C mid layer that CTTH missed,
   or is it also too thin for OCA?

4. **Render the CTTH quicklook PNGs** (already in the cached zips:
   `quicklooks/...QCK-IMAGE-CTT--PNG_...png`) — false-colour CTT for the
   full disk. They show the retrieved CTTH temperature, not the raw L1
   brightness-temperature signal; a similar palette does not make them
   scientifically equivalent. They could still aid inspection of the retrieval.

## File layout reference

```
scripts/
  fetch_mtg_ctth_route.py      # download + sample CTTH at named waypoints
  analyze_mtg_ctth_route.py    # multi-mode debug view per waypoint × timestep
  print_ctth_route_table.py    # pivot table from cached zips
  ctth_route_histogram.py      # parallax-corrected radius histogram

/tmp/eumetsat_ctth/cache/      # downloaded zips + extracted .nc files
                               # ~95 MB per 10-min product
/tmp/eumetsat_ctth/route_*.csv # CSV output from fetch_mtg_ctth_route
```

Gotchas when picking this back up:

- **The cache is on `/tmp` and is now empty** — it was cleared long ago. The
  three offline scripts (`analyze_`, `print_`, `ctth_route_histogram`) will
  find nothing until `fetch_mtg_ctth_route.py` re-downloads the window. Budget
  ~95 MB per 10-min timestep. If this gets picked up seriously, move the cache
  under `DATA_DIR` instead.
- Script defaults are **relative paths** (`data/nav.db`, `data/packs/debug/…`),
  so they only resolve from the `main/` worktree — worktrees have no `data/`.
- `eumdac`, `satpy`, `pyproj`, `xarray` are all present in `main/venv`;
  `EUMETSAT_CONSUMER_KEY` / `_SECRET` are in `main/.env` (not in `.env.sample`).

## References

- EUMETSAT Data Store user guide: https://user.eumetsat.int/resources/user-guides/data-store-detailed-guide
- EUMDAC client: https://user.eumetsat.int/resources/user-guides/eumetsat-data-access-client-eumdac-guide
- MTG L2 CTTH product page: https://navigator.eumetsat.int/product/EO:EUM:DAT:0681
- MTG OCA product page: https://navigator.eumetsat.int/product/EO:EUM:DAT:0684
- 2026-05-04 reference flight: `data/packs/debug/lsgs_lsgl_lfqb_lfqa_bilgo_lfat_egtf-2026-05-04-e2b6/`
