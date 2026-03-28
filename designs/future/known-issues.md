# Known Issues & Things to Revisit

Items to periodically review. When resolved, move to the bottom under "Resolved".

---

## Bundle endpoint memory usage

**Added:** 2026-03-27
**Location:** `src/weatherbrief/api/packs.py` — `get_bundle()`

The bundle endpoint builds the entire JSON response in memory before gzip-compressing it. For long flights (~60 route points x 3 models = 180 sounding profiles), this involves:

- Loading `cross_section.json` (can be tens of MB) fully into memory
- Calling `_build_sounding_profile()` ~180 times, each doing Pydantic model validation
- Holding the full JSON dict + serialized payload + gzip output simultaneously

For the short flights tested so far (8 points, 472KB uncompressed, 47KB gzip) this is fine. For very long routes the uncompressed bundle could reach 50-60MB, meaning ~100-200MB transient memory per concurrent request.

**Options if this becomes a problem:**
- Stream the gzip output instead of building in memory
- Pre-compute and cache the bundle on disk after each refresh
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

---

## Resolved

_(none yet)_
