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

### Météo-France Reference Charts

Source: https://donneespubliques.meteofrance.fr — carte des fronts (surface analysis with fronts and isobars). These are human-drawn by MF forecasters, considered ground truth for front positions.

Charts are issued for 00Z and 12Z, typically from the 12Z model run. Download the PNG files and save with descriptive names:

```bash
mkdir -p data/calibration/{case_name}/reference
cp ~/Downloads/chart1.png data/calibration/{case_name}/reference/17_04_00Z.png
cp ~/Downloads/chart2.png data/calibration/{case_name}/reference/17_04_12Z.png
# etc.
```

**Pending**: applied for Météo-France API access to automate chart downloads. When available, add a `fetch-mf-charts` CLI subcommand.

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

Step-by-step workflow for each new set of MF charts:

```bash
# 1. Run analysis to fetch and cache current model data
python -m weatherbrief.frontal.cli analyze

# 2. Create calibration case directory
CASE="2026-04-17_12Z"  # use ECMWF init time
mkdir -p data/calibration/$CASE/{raw,reference}

# 3. Copy cached raw data
cp data/frontal_cache/ecmwf_*.json data/calibration/$CASE/raw/ecmwf.json
cp data/frontal_cache/gfs_*.json data/calibration/$CASE/raw/gfs.json
cp data/frontal_cache/icon_*.json data/calibration/$CASE/raw/icon.json

# 4. Copy MF chart images with descriptive names
cp ~/Downloads/chart1.png data/calibration/$CASE/reference/17_04_12Z.png
# ... etc

# 5. Create expected.yaml — annotate from visual inspection
# (or ask Claude to draft from the chart images, then review)

# 6. Generate validation image and review
python -m weatherbrief.frontal.cli validate \
  --charts data/calibration/$CASE/reference/*.png \
  --times "17/04 12Z" "18/04 00Z" \
  --expected data/calibration/$CASE/expected.yaml \
  --output data/calibration/$CASE/validation.png

# 7. Score
python -m weatherbrief.frontal.cli score --case data/calibration/$CASE

# 8. Iterate: correct expected.yaml, adjust thresholds, re-run
```

## Current Algorithm Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `t_gradient_threshold` | 2.0 K/100km | T850 gradient must exceed this (absolute) |
| `te_gradient_threshold` | 4.0 K/100km | θe gradient must exceed this (absolute) |
| `anomaly_threshold` | 1.0 K/100km | Gradient must exceed the 72h time-mean by this much |
| `absolute_floor` | 2.0 K/100km | Minimum raw gradient even after anomaly check |
| `smooth_sigma` | 0.5 grid pts | Gaussian smoothing before gradient computation |
| `cross_front_threshold` | 2.0 km/h | Minimum cross-front wind for cold/warm classification |
| `_MIN_FRONTAL_FRACTION` | 0.08 (8%) | Minimum fraction of zone that must be frontal |
| `_MIN_FRONTAL_POINTS` | 8 | Absolute minimum frontal points in a zone |

## Current Baseline Scores

First calibration case: 2026-04-16 12Z (4 forecast times)

| Model | POD | FAR | CSI | Type Acc |
|-------|-----|-----|-----|----------|
| ECMWF | 24% | 74% | 14% | 80% |
| GFS | 33% | 73% | 18% | 86% |

### Key Issues Identified

**Low POD — missing fronts**:
- Atlantic/UK cold fronts not detected: T850 gradient is weak over ocean at 850hPa. These fronts are often more moisture-driven (θe) than temperature-driven. The θe channel should help but its threshold (4.0) may be too high for maritime fronts.
- Fronts at zone boundaries: a front crossing the edge of a zone may not have enough coverage fraction to trigger detection.

**High FAR — false alarms**:
- S France, Alps, N Iberia, Po Valley: orographic transient gradients survive anomaly filtering. These zones have high background variability that creates brief spikes above the time-mean.
- N Iberia (Pyrenees): persistent problem zone — thermal contrast between Iberian plateau and Bay of Biscay creates genuine transient gradients that aren't fronts.

**Classification works when detection is correct**: 80-86% type accuracy shows the cross-front wind method is sound. The main classification gap is occluded fronts, which we can't distinguish from cold/warm at a single level.

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

1. **Lower anomaly threshold for maritime zones**: Atlantic/UK zones have low background gradients — even a modest front creates a visible anomaly. Could use a zone-aware anomaly threshold or just lower the global one.

2. **Persistence filter for orographic zones**: if a zone flags as frontal for >36 consecutive hours, it's likely orographic noise, not a real front. Suppress those detections.

3. **θe channel tuning**: the θe threshold (4.0 K/100km) may be too high for maritime warm fronts but too low for Mediterranean moisture boundaries. Could try different θe thresholds for maritime vs continental zones, or use θe only for warm front detection (not cold).

4. **Minimum gradient contrast within zone**: instead of just requiring N% of a zone to exceed threshold, require a gradient *contrast* within the zone — front should show a peak surrounded by lower values, not a uniformly elevated zone (which suggests orographic).

5. **Multi-case scoring**: when we have 3+ calibration cases, score across all cases simultaneously for more robust parameter selection. Avoid overfitting to one synoptic pattern.

## Data Backup

The calibration dataset (`data/calibration/`) is gitignored (raw JSON files are ~10MB each). Back up to a persistent location:

```bash
# Example: sync to a backup location
rsync -av data/calibration/ ~/Backup/flyfun-weather-calibration/
```

The `expected.yaml` files are committed to git (force-added). The raw data and reference PNGs should be backed up separately since they're too large for git.
