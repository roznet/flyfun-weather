# Frontal Detection — Calibration Workflow

## Purpose

Iterative calibration of the frontal detection algorithm against human-drawn Météo-France surface analysis charts (carte des fronts). The goal is to tune detection thresholds and algorithm parameters to maximize detection accuracy (POD) while minimizing false alarms (FAR) across a growing dataset of real weather cases.

This is an ongoing process — each new case improves the dataset and reveals edge cases. The calibration dataset is valuable and should be backed up.

## Calibration Dataset Structure

```
data/calibration/
  {init_time}/                         # e.g. 2026-04-16_12Z
    raw/
      ecmwf.json                       # cached Open-Meteo grid response (~10MB)
      gfs.json                         # full 72h forecast, all 4947 grid points
      icon.json                        # 4 variables: T850, Td850, ws850, wd850
    reference/
      17_04_00Z.png                    # Météo-France carte des fronts images
      17_04_12Z.png                    # named as DD_MM_HHZ.png
      18_04_00Z.png
      ...
    expected.yaml                      # annotated expected zones per chart time
    validation.png                     # generated 4-column comparison image
```

### Raw Data

The raw JSON files contain the full Open-Meteo response for each model — all hourly values for `temperature_850hPa`, `dewpoint_850hPa`, `wind_speed_850hPa`, `wind_direction_850hPa` across the 0.5° European grid (51 lat × 97 lon = 4947 points, domain 35-60°N, -20 to 28°E). This allows re-running detection with any algorithm or threshold change without re-fetching from Open-Meteo.

The CLI caches raw data automatically in `data/frontal_cache/`. To populate a calibration case, copy from the cache after running analysis:

```bash
mkdir -p data/calibration/{case_name}/raw
cp data/frontal_cache/ecmwf_*.json data/calibration/{case_name}/raw/ecmwf.json
cp data/frontal_cache/gfs_*.json data/calibration/{case_name}/raw/gfs.json
cp data/frontal_cache/icon_*.json data/calibration/{case_name}/raw/icon.json
```

### Reference Charts

**Primary: DWD Bodenwetterkarte** — surface analysis with fronts drawn by DWD forecasters. Clear color-coded front lines (blue=cold, red=warm, purple=occluded), much easier to read than Météo-France carte des fronts (no confusion between isobars and front lines).

- Analysis chart: `bwk_bodendruck_na_ana.png` — current hand-drawn surface analysis
- ICON forecasts: `ico_tkboden_na_{036,048,060,084,108}.png` — model front positions
- Source: `https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten/`
- Downloaded automatically by `new-case` and `charts` subcommands
- HTTP `If-Modified-Since` caching — only re-downloads when DWD publishes new charts

**Georeferencing**: Both chart templates have fixed polar stereographic projections. Pixel↔lonlat transforms calibrated from user-provided gridline intersection coordinates (~1-3px accuracy). Zone boxes can be overlaid on charts via `charts --zones`. Calibration reference points saved in `tmp/points.csv`.

**Secondary: Météo-France carte des fronts** — still usable but harder to read (isobars and fronts look similar). Used in the first two calibration cases.

### Expected Zones (expected.yaml)

Manual annotation of which zones should have frontal activity at each chart time. This is the ground truth for scoring. Created by visual inspection of the MF charts, mapping their drawn frontal symbols to our zone grid.

Format:

```yaml
- time: "17/04 12Z"           # must match --times arg in validate/score
  hour_offset: 0              # hours from model init time
  notes: >
    Free-text description of the synoptic situation for context.
  zones:
    atlantic_north: cold      # front type: cold, warm, occluded, stationary
    uk_north_ireland: cold
    uk_south: cold
    bay_of_biscay: cold
```

**Guidelines for annotation**:
- Only list zones where a front is clearly drawn on the MF chart
- Absence means "clear" — don't list zones without fronts
- Use `cold`, `warm`, `occluded`, or `stationary` for front type
- `occluded` matches either cold or warm in scoring (since our detection can't distinguish occlusions from single-type fronts at 850hPa)
- When a front is at the edge of a zone, include the zone if the front clearly enters it
- Pressure troughs, convergence lines, and shallow boundaries without frontal symbols should NOT be annotated — they're not fronts
- The annotation should reflect what the MF chart shows, not what we think the model should detect

**Iteration**: after generating the validation image, review the "Expected" column against the MF chart column. Correct any misannotations and re-run. The pilot's eye is the authority here.

## CLI Commands

### 1. Fetch and cache data

```bash
# Fetch all 3 models (uses API key from .env, caches automatically)
python -m weatherbrief.frontal.cli analyze

# Fetch specific model
python -m weatherbrief.frontal.cli analyze --model ecmwf

# Force fresh fetch (ignore cache)
python -m weatherbrief.frontal.cli analyze --no-cache
```

### 2. Console analysis

```bash
# Zone × horizon table for all models
python -m weatherbrief.frontal.cli analyze

# Zone detail at specific hour
python -m weatherbrief.frontal.cli zones --model ecmwf --hour 24

# Route frontal table with clearance timing
python -m weatherbrief.frontal.cli route --template uk_alps

# Grid/zone info without fetching
python -m weatherbrief.frontal.cli analyze --dry-run
```

### 3. Scoring against expected zones

```bash
# Score with current defaults
python -m weatherbrief.frontal.cli score \
  --case data/calibration/2026-04-16_12Z

# Score with different thresholds (for parameter sweeps)
python -m weatherbrief.frontal.cli score \
  --case data/calibration/2026-04-16_12Z \
  --threshold 1.5 --te-threshold 6.0 --anomaly 0.8 --floor 1.5

# Score specific models
python -m weatherbrief.frontal.cli score \
  --case data/calibration/2026-04-16_12Z \
  --models ecmwf gfs icon
```

Score output includes per-time-step hit/miss/false-alarm detail plus summary metrics:
- **POD** (Probability of Detection): hits / (hits + misses)
- **FAR** (False Alarm Ratio): false alarms / (hits + false alarms)
- **CSI** (Critical Success Index): hits / (hits + misses + false alarms)
- **Type accuracy**: among hits, how often cold/warm matches expected

### 4. Visual validation (4-column comparison)

```bash
python -m weatherbrief.frontal.cli validate \
  --charts \
    data/calibration/2026-04-16_12Z/reference/17_04_12Z.png \
    data/calibration/2026-04-16_12Z/reference/18_04_00Z.png \
    data/calibration/2026-04-16_12Z/reference/18_04_12Z.png \
    data/calibration/2026-04-16_12Z/reference/19_04_00Z.png \
  --times "17/04 12Z" "18/04 00Z" "18/04 12Z" "19/04 00Z" \
  --expected data/calibration/2026-04-16_12Z/expected.yaml \
  --output data/calibration/2026-04-16_12Z/validation.png
```

Generates a 4-column image: **Météo-France** | **Expected zones** | **ECMWF** | **GFS**. Each model tile shows per-row POD/FAR/CSI scores in the annotation. The expected column renders the YAML as a zone map with C (cold, blue), W (warm, red), O (occluded, purple).

### 5. Cache management

```bash
# Clear cached grid data (forces re-fetch on next run)
python -m weatherbrief.frontal.cli clear-cache
```

## Adding a New Calibration Case

Automated with the `new-case` subcommand:

```bash
# 1. Create case: fetches all models, downloads DWD charts, generates zone overlay
python -m weatherbrief.frontal.cli new-case
# Auto-names from ECMWF init time, or use --name 2026-04-18_06Z

# 2. Open the zone overlay to identify fronts per zone
open data/calibration/2026-04-18_06Z/reference/analysis_with_zones.png

# 3. Edit expected.yaml — annotate zones from the DWD chart
# The skeleton is pre-populated with chart times and hour offsets

# 4. Score
python -m weatherbrief.frontal.cli score --case data/calibration/2026-04-18_06Z

# 5. Iterate: correct expected.yaml, adjust thresholds, re-run
```

For visual validation with the 4-column comparison (reference | expected | ECMWF | GFS), use the `validate` subcommand as before.

## Current Algorithm Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `t_gradient_threshold` | 2.0 K/100km | T850 gradient must exceed this (absolute) |
| `te_gradient_threshold` | 4.0 K/100km | θe gradient must exceed this (absolute) |
| `anomaly_threshold` | 1.0 K/100km | T850 gradient must exceed the 72h time-mean by this much |
| `absolute_floor` | 2.0 K/100km | Minimum raw T850 gradient even after anomaly check |
| `te_anomaly_threshold` | 2.0 K/100km | θe anomaly threshold (default: 2× T anomaly) |
| `te_absolute_floor` | 4.0 K/100km | θe minimum floor (default: 2× T floor) |
| `smooth_sigma` | 0.5 grid pts | Gaussian smoothing before gradient computation |
| `cross_front_threshold` | 2.0 km/h | Minimum cross-front wind for cold/warm classification |
| `_MIN_FRONTAL_FRACTION` | 0.08 (8%) | Minimum fraction of zone that must be frontal |
| `_MIN_FRONTAL_POINTS` | 8 | Absolute minimum frontal points in a zone |

## Current Baseline Scores

First calibration case: 2026-04-16 12Z (4 forecast times)

| Model | POD | FAR | CSI | Type Acc |
|-------|-----|-----|-----|----------|
| ECMWF | 57% | 77% | 19% | 83% |
| GFS | 62% | 76% | 21% | 69% |

Previous scores (before per-channel anomaly fix): ECMWF POD=24%, GFS POD=33%.

### Key Issues Identified

**POD improved significantly with per-channel anomaly filtering**: the original anomaly filter used only the T850 gradient for all points, which killed θe-detected maritime/warm fronts. The fix applies anomaly filtering per-channel (T points vs T background, θe points vs θe background), with θe thresholds scaled 2× to account for naturally higher θe gradients.

**Remaining misses**:
- Atlantic oceanic fronts: genuinely weak gradients at 850hPa over open ocean. T850 gradient max ~1.6 K/100km (below 2.0 threshold), θe gradient barely reaches 4.5. These fronts are more visible in surface analysis (isobars, wind shift) than at 850hPa.
- uk_south at some timesteps: front is narrow and at zone boundary, fraction check fails even when a few points detect it.

**High FAR — false alarms** (main remaining problem):
- θe channel generates widespread transient anomalies in Mediterranean and continental zones. Even with 2× scaled thresholds, zones like Balearics, W Med, S France, N Iberia frequently trigger.
- The persistence filter (proposed in calibration strategy) would help — orographic θe spikes that persist >36h should be suppressed.
- S France, Alps, N Iberia, Po Valley: orographic transient gradients survive anomaly filtering.

**Classification**: 83% type accuracy on ECMWF when detection is correct. Cross-front wind method is sound. GFS type accuracy lower (69%) — likely due to different wind field biases.

## Calibration Strategy

### Parameter sweep approach

With the `score` command's CLI args, run systematic sweeps:

```bash
for thresh in 1.5 2.0 2.5; do
  for anomaly in 0.5 0.8 1.0 1.5; do
    echo "=== threshold=$thresh anomaly=$anomaly ==="
    python -m weatherbrief.frontal.cli score \
      --case data/calibration/2026-04-16_12Z \
      --threshold $thresh --anomaly $anomaly \
      2>&1 | grep -E "POD|FAR|CSI"
  done
done
```

### Algorithm improvements to explore

### Tried and evaluated

1. **Per-channel anomaly filtering** ✅ IMPLEMENTED — each channel (T, θe) filtered against its own background. Fixed the original bug where T background killed θe-detected maritime fronts. POD improved from 24% to 57-100%.

2. **Persistence filter** ❌ DIDN'T HELP — infrastructure exists (`_apply_persistence_filter`, default 72h) but disabled. Real fronts persist 30-50h in zones, false alarms 80-96h — no threshold separates them without killing real detections.

3. **TFP proximity filter (Hewson)** ❌ DIDN'T HELP — tested at both 0.5° and 0.25° resolution with various smoothing. TFP zero-crossings are too ubiquitous; false alarm zones have genuine gradient peaks from moisture boundaries. Infrastructure exists but disabled (`tfp_dilation=-1`).

4. **Fraction-based suppression** ❌ TOO BLUNT — suppressing zones active >80% of time also removes zones with real fronts (bay_of_biscay, atlantic_south).

5. **Higher detection thresholds** ⚡ PROMISING — T=3.0, θe=6.0, anomaly=2.0, floor=3.0 drops FAR from 77% to 57% at cost of missing weak fronts. For GA briefings, weak fronts are low consequence. Needs validation with strong-front cases before changing defaults.

### Still to explore

1. **Wind shift criterion** — require significant wind direction change across the gradient. Synoptic fronts have baroclinic wind shifts; moisture boundaries don't.

2. **Spatial coherence** — require frontal signal to be coherent over ~200km minimum length. Fronts are linear; false alarms are patchy.

3. **Multi-case scoring** — when we have 5+ cases spanning different synoptic regimes, score across all simultaneously. Current 2 cases are both weak-front patterns.

4. **METAR FROPA validation** — use frontal passage reports from European METAR stations as aviation-native ground truth. Need to verify FROPA availability in EASA-region METARs.

## Route-Scale Hewson Calibration (Real-Flight Pairs)

The sections above cover **zone-scale** detection against drawn fronts on DWD/MF charts. This section covers a separate, complementary calibration track: **route-scale Hewson metrics** validated against pilot-reported flight outcomes. The two are distinct:

| Aspect | Zone calibration (above) | Route Hewson calibration (this section) |
|---|---|---|
| Target | "Is there a front in zone X?" | "What does the metric look like along leg Y, and did the pilot feel it?" |
| Ground truth | DWD chart annotation | Pilot debrief — front felt vs not felt |
| Output unit | POD / FAR / CSI per zone | Metric values per waypoint × per hour |
| Best for | Synoptic narrative, LLM digest | Per-leg advisory thresholds (§3 of Hewson design doc) |

Both are expected to coexist — zone scoring tunes the zone aggregator, route scoring tunes per-leg evaluators (Phase C).

### How to run a route-scale calibration pair

1. **Backdate the precompute** for the flight day(s):
   ```bash
   python -m weatherbrief.hewson precompute --date YYYY-MM-DD --stride-hours 1
   ```
   Writes one NPZ per model at `data/hewson/<model>/YYYY-MM-DDT00:00:00Z.npz`. Open-Meteo's customer-api serves all 3 models historically with the project key, so backdate is cheap (~3 minutes for 3 models, ~80 MB total).

2. **Sample the route(s)** through the snapshot. Reference implementation in `scripts/compare_hewson_may1_may4.py`:
   - Loads the NPZ via `numpy.load`
   - Resolves ICAO/waypoint codes via `weatherbrief.airports.resolve_waypoints`
   - Inserts mid-leg interpolated points on legs of interest (catches sub-leg TFP zero-crossings)
   - Bilinear-samples each metric at 925 / 850 hPa for the flight's hour window
   - Prints colored thresholds per §3 of `designs/future/hewson-fields-aviation-advisories.md`

3. **Compare to pilot recollection.** A pair of flights — one with a felt front, one smooth — gives the strongest discrimination signal.

### Findings — May 1 / May 4 2026 calibration pair

First real-flight pair, run 2026-05-06.

- **May 4** LSGS → LSGL → LFQB → LFQA → BILGO → LFAT → EGTF (07-12 Z). Pilot reported front Dijon → north of Reims; UK side smooth.
- **May 1** EGTF → LFQA → LFSD → LSGL → LSGS (07-11 Z). Pilot reported smooth, no front.

**1. TFP sign progression along the route is the discriminator, not |∇θe| magnitude.**

May 4 ECMWF @ 925 hPa, in route order south → north:

| Waypoint | Lat / Lon | TFP (K/100km²) |
|---|---|---|
| LSGS (Sion) | 46.22 N, 7.33 E | +0.79 |
| LSGL (Lausanne) | 46.55 N, 6.62 E | **+1.96** |
| .LSGL-LFQB.1/3 | 47.14 N, 5.75 E | **−1.52** ← Dijon — sign change |
| .LSGL-LFQB.2/3 | 47.73 N, 4.88 E | −1.59 |
| LFQB (Troyes) | 48.32 N, 4.02 E | +0.17 |
| LFQA (Reims) | 49.21 N, 4.16 E | +0.83 |
| BILGO | 49.90 N, 3.45 E | +0.78 |
| LFAT (Le Touquet) | 50.52 N, 1.62 E | < 0.1 |
| EGTF (Fairoaks) | 51.35 N, −0.56 E | −0.17 |

Clean **+ → − → +** crossing right at Dijon, then quiet to UK. Pilot's report exactly.

May 1 ECMWF @ 925 hPa for comparison: TFP at LFQA was −0.63, but the gradient magnitude |∇θe| there was +9.21 — comparable to anywhere on May 4. The TFP sign zigzagged + → − → + → + → − incoherently across the route (no single coherent crossing). Magnitude alone would have fired a false advisory.

**2. 925 hPa is sharper than 850 hPa for GA-altitude fronts.**

| Model | May 4 LSGL→LFQB leg, |∇θe| 925 hPa | … 850 hPa |
|---|---|---|
| ECMWF | +4.42 → +7.01 (mid) | +3.35 → +7.85 (mid) |
| GFS | +2.89 (warm) → −1.25 (cold) — caught the front | max +3.5 — **missed it** |
| ICON | +6.08 → +4.69 (mid) | +4.63 → +4.07 (mid) |

GFS is the headline: at 850 hPa it shows almost no signal across the LFQB-LFQA segment despite the front being there; at 925 hPa the TFP crosses cleanly. Boundary-layer moisture is concentrated at 925 (~2,500 ft), which aligns with what a GA pilot at FL060-FL080 actually feels.

**3. Mid-leg interpolation is necessary to localise the front.**

Without intermediate sample points, the LSGL → LFQB leg is ~200 km — the TFP sign change would be detected but the zero-crossing position would be ambiguous within that range. Inserting two evenly-spaced intermediate points (1/3 and 2/3 along the leg) put the crossing at 47.14 N, 5.75 E — within ~10 nm of Dijon. Recommend ≥2 intermediate samples per leg of interest in any production advisory evaluator.

**4. §10a.1 (Hewson ≠ cloud) is the real gap.**

May 1 LFQA showed |∇θe| ≥ 11 K/100km on all 3 models (ICON peaked at 28 K/100km — likely regridding artifact at coastal cells, separately worth flagging). Yet the flight was smooth. The boundary was real but **dry** — no operational impact. This validates the Phase E moisture cross-check (RH₉₂₅ + LCC + TP) as the missing ingredient before §3 advisory thresholds can be wired into evaluators.

**5. ICON Open-Meteo regridding artifact.**

ICON shows |∇θe| of 25-29 K/100km at LFQA on May 1 — that is non-physical at synoptic scale. Likely an interpolation edge effect at the coastal/channel grid cell. Consistent with `project_open_meteo_ecmwf_discrepancy` in private memory. Worth checking whether ICON-EU regridded by Open-Meteo has known issues at coastal cells before consuming this metric in advisories.

**6. Model ranking against ground truth (this pair).**

For European GA fronts: **ECMWF ≈ ICON > GFS** at 925 hPa. ECMWF and ICON both saw the May 4 front cleanly; GFS only saw it at 925 hPa (missed at 850). Consistent with pilot's separate recollection that GFS was further from observed reality than ECMWF/ICON on the day.

### Implications for Phase C advisory evaluators

When wiring §3 thresholds from `designs/future/hewson-fields-aviation-advisories.md` into per-leg evaluators:

- **Sample at 925 hPa primary** for low-level GA flights, fall back to 850 hPa for higher cruise. The 3-level precompute supports this directly.
- **Use TFP sign progression along the leg** (count zero-crossings within the leg) as the structural detector, not raw |∇θe| max. Magnitude becomes a secondary intensity score after the sign-progression check.
- **Aggregate with mid-leg sampling** (P95 or sign-change-count rather than max-of-2-endpoints) so a leg longer than ~150 km doesn't miss a front that crosses through its middle.
- **Suppress on dry signature** once Phase E lands. Until then, advisories will fire on May-1-style dry boundaries.

### Calibration data inventory

| Date | Route | Outcome | Snapshots on disk |
|---|---|---|---|
| 2026-05-01 | EGTF → LFQA → LFSD → LSGL → LSGS | smooth | data/hewson/{ecmwf,gfs,icon}/2026-05-01T00:00:00Z.npz |
| 2026-05-04 | LSGS → LSGL → LFQB → LFQA → BILGO → LFAT → EGTF | felt front Dijon → N. Reims | data/hewson/{ecmwf,gfs,icon}/2026-05-04T00:00:00Z.npz |

Reproducer: `scripts/compare_hewson_may1_may4.py`.

Future pairs should follow the same shape: capture route + hour window + pilot debrief tag (smooth / front-felt / IMC / turbulence-only / convective). When we have 5-10 pairs across different synoptic regimes, run the same evaluator-threshold sweep this section already does for the zone calibration.

## Data Backup

The calibration dataset (`data/calibration/`) is gitignored (raw JSON files are ~10MB each). Back up to a persistent location:

```bash
# Example: sync to a backup location
rsync -av data/calibration/ ~/Backup/flyfun-weather-calibration/
```

The `expected.yaml` files are committed to git (force-added). The raw data and reference PNGs should be backed up separately since they're too large for git.
