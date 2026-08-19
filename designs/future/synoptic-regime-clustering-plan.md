# Synoptic Regime Clustering — Implementation Plan for FlyFun Weather

## Overview

### Goal

FlyFun Weather serves European GA pilots planning cross-country flights across a wide range of routes: UK to the Alps and Mediterranean, Germany to Italy and the Adriatic, Benelux to Iberia, Scandinavia south. These pilots are currently underserved by existing tools — ForeFlight dominates the US market but Europe has no equivalent product that combines multi-model forecast comparison with synoptic context at the GA level.

This plan covers synoptic regime classification: tagging each forecast day with the prevailing large-scale atmospheric regime. Two regional domains are classified independently:

- **Atlantic domain** (NAO+, NAO-, Scandinavian Blocking, Atlantic Ridge)
- **Mediterranean domain** (Zonal, Cut-off Low, Anticyclonic, Northerly/Mistral)

Both domains are always classified. For each airport, the domain with the higher separation score is selected — the atmosphere itself tells you which large-scale pattern is most relevant. This eliminates the need for a hard geographic boundary and naturally handles Alpine and transitional stations.

Over time, accumulate model reconciliation data stratified by regime to determine which NWP model (GFS, ECMWF, ICON) performs best under each regime at each forecast horizon. Use this to produce regime-conditioned model weights for blended forecasts.

Frontal detection and route corridor analysis are covered separately — that work has since shipped, so read [`designs/frontal-detection.md`](../frontal-detection.md) for current truth (`frontal-detection-plan.md` in this folder is now historical).

### Status (2026-08-15)

Still an unbuilt future plan, and still a live idea. Nothing has moved on it: there is no `src/weatherbrief/regime/` module, no `weatherbrief.regime` CLI, and no regime columns on any verification table — Parts 2–6 are entirely future work. Promote this to a real design doc (and an INDEX entry) only once the `regime` module ships.

What the surrounding code now gives you for free, verified against the tree:

- **ERA5 bulk download is a solved problem in-repo.** `scripts/download_era5_hewson.py` (tracked, with `scripts/smoke_era5_hewson.py` as a single-day shakeout) is a working, resumable, monthly-chunk CDS downloader — same shape as the Part 1 sketch below, already debugged against the post-2024 CDS. Copy that script rather than the illustrative code in §1.2, and change only the request payload: regime needs `geopotential` at 500 hPa, `2.5°` grid, 12 UTC only, area `[70, -40, 30, 40]`, 1991–2020; the Hewson one pulls t/q/u/v at 925/850/700 on `0.25°`, 4×/day, area `[60, -20, 35, 28]`. The old untracked `tmp/regime/` prototypes (`download_era5.py`, `download_era5_t850.py`, `smoke_test_era5.py`) are superseded by it.
- **ERA5 GRIB reading exists**: `src/weatherbrief/era5/loader.py` (`load_era5_fields`) — but it is pressure-level t/q/u/v → the frontal field dict, not Z500. Useful as a cfgrib/xarray reference, not directly reusable.
- **The live-data dependency is confirmed present.** `fetch_multi_point()` in `src/weatherbrief/fetch/open_meteo.py` takes a `chunk_size` override (added for exactly this kind of short-parameter-list caller), and `geopotential_height` at 500 hPa is in the level lists for all three models in `src/weatherbrief/fetch/variables.py` (`ECMWF_PRESSURE_LEVELS`, `EXTENDED_PRESSURE_LEVELS` for GFS, `ICON_PRESSURE_LEVELS`). It lands on `PressureLevelData.geopotential_height_m`.

### Architecture summary

The system has three distinct phases:

1. **One-time ERA5 download**: bulk-fetch 30 years of Z500 data from Copernicus CDS (runs on MacBook, data stays on MacBook + NAS)
2. **One-time calibration**: compute climatology, fit PCA + k-means for both domains, persist small artefact files (runs on MacBook, artefacts deployed to prod)
3. **Daily classification**: fetch live Z500 from Open-Meteo, classify today's regime and predict T+24/48/72 per model, tag verification records

### Data and artefact separation

| What | Location | Size | Persisted where |
|---|---|---|---|
| ERA5 raw GRIB files (30 years) | MacBook + NAS | ~500MB–1GB | Never on prod |
| Calibration scripts | Git repo | — | Repo |
| **Output artefacts** (climatology, PCA, k-means, labels, domain metadata) | Configured via `REGIME_ARTEFACTS_DIR` env var | ~few MB | Prod + dev |
| Daily Z500 fields | Ephemeral, fetched via Open-Meteo | — | In-memory only |

### Output surfaces

| Component | Where it appears |
|---|---|
| Regime label + confidence | TBD — initially CLI output only, pipeline integration later |
| Model skill weighting | Backend only — regime-conditioned blending of GFS/ECMWF/ICON, invisible to user |

---

## Part 1 — One-Time ERA5 Download

### 1.1 Data Source: ERA5 via Copernicus CDS

**Where**: Copernicus Climate Data Store — `cds.climate.copernicus.eu`

**Registration**: Free account required. Must accept the ERA5 dataset licence via the web UI before the API will work. No cost.

**Authentication (new CDS, post-2024 migration)**: Generate a Personal Access Token (PAT) from your CDS profile page. Configure `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-personal-access-token>
```

Requires `cdsapi >= 0.7.0`.

**What to pull** (both domains use the same download — the bounding box covers both):
- Variable: `geopotential` (z) on pressure level `500 hPa`
- Time: daily, 12 UTC snapshot only
- Period: 1991–2020 (30-year WMO standard climatology period)
- Combined domain: `30N–70N, 40W–40E` (covers both Atlantic and Mediterranean domains)
- Resolution: `2.5°` — sufficient for synoptic-scale features; higher resolution adds noise
- Format: GRIB (faster retrieval than NetCDF, server-side conversion avoided)

### 1.2 Download Strategy

CDS requests are queued, not instant. A single 30-year request will likely timeout or get deprioritized. The recommended approach:

**Chunk by month** — submit one request per calendar month = ~360 requests total. Each produces a small GRIB file (~10-50MB).

**Submit in parallel** — the CDS allows ~10-15 concurrent requests per user. Submit a batch, poll for completion, download finished ones, submit more.

**Expected timeline**: 1-3 days with parallel submissions. Sequential would take 1-2 weeks.

The code below is illustrative only — `scripts/download_era5_hewson.py` already implements this pattern (resumable, `--max-concurrent`, monthly chunks) and is the thing to copy.

```python
import cdsapi
import calendar
import time
from pathlib import Path

def submit_era5_downloads(output_dir: str, max_concurrent: int = 12):
    """
    Submit ERA5 Z500 downloads in monthly chunks with parallel processing.
    Uses wait_until_complete=False for non-blocking submission.
    """
    client = cdsapi.Client(wait_until_complete=False)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pending = []

    for year in range(1991, 2021):
        for month in range(1, 13):
            target = output_path / f'era5_z500_{year}_{month:02d}.grib'
            if target.exists():
                print(f"Skipping {target.name} (already exists)")
                continue

            _, last_day = calendar.monthrange(year, month)

            # Wait if we have too many concurrent requests
            while len(pending) >= max_concurrent:
                pending = _poll_and_download(pending)
                if len(pending) >= max_concurrent:
                    time.sleep(30)

            result = client.retrieve(
                'reanalysis-era5-pressure-levels',
                {
                    'product_type': 'reanalysis',
                    'variable': 'geopotential',
                    'pressure_level': '500',
                    'year': str(year),
                    'month': f'{month:02d}',
                    'day': [f'{d:02d}' for d in range(1, last_day + 1)],
                    'time': '12:00',
                    'area': [70, -40, 30, 40],   # N, W, S, E
                    'grid': [2.5, 2.5],
                    'data_format': 'grib',
                },
            )
            pending.append((result, str(target)))
            print(f"Submitted {target.name}")

    # Wait for remaining
    while pending:
        pending = _poll_and_download(pending)
        if pending:
            time.sleep(30)


def _poll_and_download(pending: list) -> list:
    """Check pending requests, download completed ones, return still-pending."""
    still_pending = []
    for result, target in pending:
        result.update()
        state = result.reply.get('state', 'unknown')
        if state == 'completed':
            result.download(target)
            print(f"Downloaded {Path(target).name}")
        elif state == 'failed':
            print(f"FAILED: {target} — {result.reply}")
        else:
            still_pending.append((result, target))
    return still_pending
```

**Gotchas**:
- If you have an old-format `.cdsapirc` (UID:key pair), the new CDS will return cryptic auth errors
- The `data_format` parameter replaces the old `format` parameter (though `format` may still work)
- Requests for older data (1990s) may need tape retrieval and take longer
- Verify output file sizes after download — the system occasionally produces truncated files silently

### 1.3 Merge Monthly Files

After all monthly GRIB files are downloaded, merge into a single dataset for calibration:

```python
import xarray as xr
from pathlib import Path

def merge_era5_monthly(input_dir: str, output_file: str):
    """Merge monthly ERA5 GRIB files into a single NetCDF for calibration."""
    grib_files = sorted(Path(input_dir).glob('era5_z500_*.grib'))
    print(f"Merging {len(grib_files)} files...")

    ds = xr.open_mfdataset(
        grib_files, engine='cfgrib',
        combine='by_coords',
        parallel=True,
    )
    ds.to_netcdf(output_file)
    print(f"Merged to {output_file}")
```

---

## Part 2 — One-Time Calibration

Both domains (Atlantic and Mediterranean) follow the same calibration pipeline. The steps below show the Atlantic domain; the Mediterranean domain uses the same code with different spatial subsetting and cluster labels.

### 2.1 Domain Definitions

```python
DOMAINS = {
    'atlantic': {
        'lat_min': 30, 'lat_max': 70,
        'lon_min': -40, 'lon_max': 40,
        'k': 4,
        'expected_labels': {
            # Assign after visual inspection of centroid plots
            # These are placeholders based on the standard ECMWF 4-regime framework
            0: 'nao_plus',
            1: 'nao_minus',
            2: 'blocking',
            3: 'atlantic_ridge',
        },
        'description': 'Euro-Atlantic regime classification',
    },
    'mediterranean': {
        'lat_min': 30, 'lat_max': 50,
        'lon_min': -10, 'lon_max': 30,
        'k': 4,
        'expected_labels': {
            # Assign after visual inspection of centroid plots
            0: 'zonal',
            1: 'anticyclonic',
            2: 'cutoff_low',
            3: 'northerly_mistral',
        },
        'description': 'Mediterranean regime classification',
    },
}
```

**Atlantic domain** (`30N-70N, 40W-40E`): ~17 lat × 33 lon = **561 grid points** at 2.5°. Captures the Icelandic low, Azores high, and full European continent.

**Mediterranean domain** (`30N-50N, 10W-30E`): ~9 lat × 17 lon = **153 grid points** at 2.5°. Covers Iberia to Turkey, Saharan boundary to Alps.

**Expected Mediterranean patterns**:

| Regime | Signature |
|---|---|
| **Zonal** | Westerlies crossing the Med, no blocking. Active fronts. |
| **Anticyclonic** | Ridge over western/central Med. Clear, stable. Classic VFR. |
| **Cut-off Low** | Closed low detached from jet, usually over Iberia or Gulf of Genoa. Persistent bad weather, hard to forecast. |
| **Northerly/Mistral** | Strong pressure gradient channeling north wind through Rhône valley. Clear but turbulent. |

**Airport-to-domain mapping**: both domains are always classified. The domain with the higher separation score is used for each airport's regime tag. This self-adapts: a Nice airport under Mistral conditions gets Mediterranean classification (strong Mistral pattern = high separation), but under a deep NAO- trough pushing south, the Atlantic classifier may score higher. When both domains have high separation, both regime labels are stored — the verification system can track skill against either.

### 2.2 Compute Climatological Mean

For each grid point and each calendar day-of-year (1–366), compute the mean geopotential height across all 30 years. This is the baseline you subtract to get the anomaly.

**Important**: smooth the climatology with a 30-day rolling window to avoid noisy estimates for rare dates and day-to-day jumpiness.

```python
import xarray as xr
import numpy as np

def compute_climatology(merged_file: str, domain: dict, output_file: str):
    ds = xr.open_dataset(merged_file)
    z500 = ds['z'].sel(
        latitude=slice(domain['lat_max'], domain['lat_min']),
        longitude=slice(domain['lon_min'], domain['lon_max']),
    )

    # Convert geopotential to geopotential height (divide by g)
    z500_height = z500 / 9.80665

    # Compute day-of-year climatology with 30-day smoothing
    clim = z500_height.groupby('time.dayofyear').mean('time')

    # Smooth across day-of-year axis to remove noise
    # Use a 30-day rolling window, wrapping at year boundaries
    clim_smooth = clim.rolling(dayofyear=30, center=True, min_periods=1).mean()

    clim_smooth.to_netcdf(output_file)
    return z500_height, clim_smooth
```

### 2.3 Compute Anomaly Fields

Subtract the smoothed climatology from each day's raw field.

```python
def compute_anomalies(z500_height, clim_smooth):
    anomaly = z500_height.groupby('time.dayofyear') - clim_smooth

    # Flatten spatial dimensions: shape becomes (N_days, N_gridpoints)
    n_days = anomaly.shape[0]
    n_points = anomaly.shape[1] * anomaly.shape[2]
    X = anomaly.values.reshape(n_days, n_points)

    # Remove any days with NaN (e.g. Feb 29 in non-leap years)
    valid_mask = ~np.isnan(X).any(axis=1)
    X = X[valid_mask]
    dates = anomaly.time.values[valid_mask]

    print(f"Training matrix shape: {X.shape}")
    # Atlantic: expect (~10950, ~561)
    # Mediterranean: expect (~10950, ~153)
    return X, dates
```

### 2.4 PCA Dimensionality Reduction

Adjacent grid points are highly correlated. PCA reduces the high-dimensional space to a handful of orthogonal components that capture the dominant patterns of variability.

```python
from sklearn.decomposition import PCA
import joblib

def fit_pca(X: np.ndarray, n_components: int = 10, output_file: str = None):
    pca = PCA(n_components=n_components, random_state=42)
    X_reduced = pca.fit_transform(X)

    print(f"Explained variance by component: {pca.explained_variance_ratio_}")
    print(f"Cumulative: {pca.explained_variance_ratio_.cumsum()}")

    if output_file:
        joblib.dump(pca, output_file)

    return pca, X_reduced
```

**Expected result**: the first 2–3 components typically explain 40–60% of variance; 8–10 components usually cover 80%+. For the Atlantic domain, the first EOF corresponds to NAO, the second to the Atlantic Ridge / Scandinavian blocking pattern.

### 2.5 K-Means Clustering

Cluster the PCA-reduced daily vectors into k regimes. k=4 for both domains (matching the established ECMWF 4-regime framework for Atlantic; 4 physically-motivated patterns for Mediterranean).

```python
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

def fit_kmeans(X_reduced: np.ndarray, target_k: int = 4, output_file: str = None):
    # Try a range around target k, validate with silhouette score
    results = {}
    for k in range(max(3, target_k - 1), target_k + 3):
        km = KMeans(n_clusters=k, random_state=42, n_init=20)
        labels = km.fit_predict(X_reduced)
        score = silhouette_score(X_reduced, labels, sample_size=2000)
        results[k] = {'kmeans': km, 'labels': labels, 'silhouette': score}
        print(f"k={k}: silhouette={score:.3f}")

    best_k = target_k  # Use target unless scores strongly suggest otherwise
    kmeans = results[best_k]['kmeans']
    labels = results[best_k]['labels']

    if output_file:
        joblib.dump(kmeans, output_file)

    return kmeans, labels, results
```

### 2.6 Label the Clusters Manually

This is a required manual step. Plot each cluster centroid as a 2D map and assign a human-readable label based on the spatial pattern.

```python
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import numpy as np

def plot_centroids(pca, kmeans, labels, lat, lon, domain: dict, output_file: str):
    k = kmeans.n_clusters
    cols = min(k, 4)
    rows = (k + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows),
                              subplot_kw={'projection': ccrs.PlateCarree()})
    if k == 1:
        axes = [axes]
    else:
        axes = axes.flat

    for i, ax in enumerate(axes):
        if i >= k:
            ax.set_visible(False)
            continue
        centroid_grid = pca.inverse_transform(kmeans.cluster_centers_[i])
        centroid_map = centroid_grid.reshape(len(lat), len(lon))

        ax.set_extent([domain['lon_min'], domain['lon_max'],
                       domain['lat_min'], domain['lat_max']])
        ax.coastlines()
        cf = ax.contourf(lon, lat, centroid_map, levels=20,
                         cmap='RdBu_r', transform=ccrs.PlateCarree())
        plt.colorbar(cf, ax=ax, label='Z500 anomaly (m)')
        ax.set_title(f'Cluster {i} — N={np.sum(labels==i)} days')

    plt.tight_layout()
    plt.savefig(output_file, dpi=150)
    plt.show()
```

**Expected Atlantic patterns**:

| Pattern | Signature |
|---|---|
| **NAO+** | Strong negative anomaly over Iceland, positive over Azores. Strong westerlies into Europe. |
| **NAO-** | Reversed: positive over Greenland/Iceland, negative over Atlantic. Blocking, cold outbreaks. |
| **Scandinavian Blocking** | Strong positive anomaly over Scandinavia/Russia. Persistent high. |
| **Atlantic Ridge** | Positive anomaly over central Atlantic, steering storms north of UK. |

After visual inspection, create the label mapping:

```python
import json

# Edit these after looking at the centroid plots for each domain
REGIME_LABELS = {
    0: 'nao_plus',
    1: 'blocking',
    2: 'nao_minus',
    3: 'atlantic_ridge',
}

with open('regime_labels.json', 'w') as f:
    json.dump(REGIME_LABELS, f)
```

### 2.7 Compute Training Distance Statistics (for confidence metric)

The confidence metric needs a reference distribution from the training data to detect outliers and calibrate separation scores.

```python
def compute_training_stats(kmeans, X_reduced: np.ndarray, output_file: str):
    """
    Compute per-cluster distance statistics from training data.
    Used by the confidence metric to calibrate scores and detect outliers.
    """
    all_distances = kmeans.transform(X_reduced)  # (n_samples, k) distances
    nearest_distances = all_distances.min(axis=1)
    labels = kmeans.predict(X_reduced)

    stats = {
        'global_median_nearest': float(np.median(nearest_distances)),
        'global_p95_nearest': float(np.percentile(nearest_distances, 95)),
        'per_cluster': {},
    }
    for i in range(kmeans.n_clusters):
        mask = labels == i
        cluster_distances = nearest_distances[mask]
        stats['per_cluster'][str(i)] = {
            'median_nearest': float(np.median(cluster_distances)),
            'p95_nearest': float(np.percentile(cluster_distances, 95)),
            'count': int(mask.sum()),
        }

    with open(output_file, 'w') as f:
        json.dump(stats, f, indent=2)

    return stats
```

### 2.8 Store Persisted Artefacts

All fitted objects are stored in the artefacts directory (configured via `REGIME_ARTEFACTS_DIR` env var). Structure:

```
$REGIME_ARTEFACTS_DIR/
    atlantic/
        climatology.nc           # 366 x lat x lon smoothed climatology
        pca.joblib               # fitted sklearn PCA object
        kmeans.joblib            # fitted sklearn KMeans object
        labels.json              # {cluster_id: label_string}
        training_stats.json      # distance statistics for confidence calibration
        domain.json              # {lat_min, lat_max, lon_min, lon_max, grid_res}
    mediterranean/
        climatology.nc
        pca.joblib
        kmeans.joblib
        labels.json
        training_stats.json
        domain.json
```

The `domain.json` file records the exact grid parameters so the daily pipeline can verify it's using consistent coordinates:

```json
{
    "lat_min": 30, "lat_max": 70,
    "lon_min": -40, "lon_max": 40,
    "grid_res": 2.5,
    "pressure_level": 500,
    "sklearn_version": "1.4.2"
}
```

**Deployment**: artefacts are rsynced to prod separately from the code deploy:

```bash
rsync -avz ./regime_artefacts/ brice@161.35.35.15:/mnt/flyfun_data/weather/regime/
```

On prod, `REGIME_ARTEFACTS_DIR=/mnt/flyfun_data/weather/regime` in `.env`.

**Note on sklearn version**: `joblib`-serialized sklearn objects are sensitive to version changes. Record the sklearn version used for fitting. If the production environment upgrades sklearn, re-run calibration.

### 2.9 Validate Cluster Quality

Before putting the classifier into production, run basic validation:

```python
def validate_clusters(labels, dates, regime_labels: dict):
    from collections import Counter
    import pandas as pd

    # Check regime frequency distribution (should be roughly balanced, 20-30% each for k=4)
    freq = Counter(labels)
    for cluster_id, count in sorted(freq.items()):
        pct = count / len(labels) * 100
        label = regime_labels[str(cluster_id)]
        print(f"Cluster {cluster_id} ({label}): {count} days ({pct:.1f}%)")

    # Check seasonal distribution (regimes should have seasonal preferences)
    df = pd.DataFrame({'date': dates, 'regime': labels})
    df['month'] = pd.to_datetime(df['date']).dt.month
    print(df.groupby(['month', 'regime']).size().unstack())

    # Spot-check: print 5 random member dates per cluster
    for i in range(len(regime_labels)):
        member_dates = dates[labels == i]
        sample = np.random.choice(member_dates,
                                  size=min(5, len(member_dates)), replace=False)
        print(f"\nCluster {i} ({regime_labels[str(i)]}) sample dates:")
        for d in sample:
            print(f"  {d}")
```

**Watch for seasonal artifacts**: if a cluster looks like "generic summer" rather than a distinct circulation pattern (roughly equal representation across months, weak anomaly amplitude in centroid), it's a seasonal artifact rather than a real regime. This is more likely for the Mediterranean domain where summer patterns are weaker. If this happens, consider increasing k or excluding JJA from training.

---

## Part 3 — Daily Classification via Open-Meteo

### 3.1 Live Z500 Source: Open-Meteo

The existing Open-Meteo client (`src/weatherbrief/fetch/open_meteo.py`) already fetches `geopotential_height` at 500hPa for GFS, ECMWF, and ICON via `fetch_multi_point()`. The regime classifier reuses this infrastructure.

**Grid requirements** (both domains always fetched):

| Domain | Grid points | Chunks (at ~300/chunk) | API calls per model |
|---|---|---|---|
| Atlantic (30-70N, 40W-40E at 2.5°) | 561 | 2 | 2 |
| Mediterranean (30-50N, 10W-30E at 2.5°) | 153 | 1 | 1 |
| **Total per cycle** (3 models) | 714 | 3 per model | **9** |

The chunk size can be larger here than the default 150 (which was constrained by URL length with many variables). For regime classification we only request `geopotential_height_500hPa`, so URLs are short and ~300 points per chunk is safe.

This is a trivial load — ~9 API calls per cycle, well within rate limits.

**Implementation**: a thin wrapper that defines the 2.5° grid points for each domain, calls `fetch_multi_point()` requesting only the 500hPa geopotential_height variable, and reshapes the response into a 2D lat×lon array matching the ERA5 climatology grid.

### 3.2 Confidence Metric

The confidence metric uses two complementary measures:

**Separation score** (0–1): how much closer is the best-matching regime vs. the runner-up? Based on the nearest/second-nearest distance ratio (analogous to Lowe's ratio test in feature matching):

```
separation = 1.0 - (d_nearest / d_second_nearest)
```

- `separation = 0.0`: equidistant between two regimes (ambiguous, likely transitioning)
- `separation = 0.8`: nearest centroid is 5× closer than second-nearest (confident assignment)

**Outlier flag**: is this day's pattern within the training distribution at all? Compare the nearest-centroid distance to the 95th percentile from training data:

```
is_outlier = d_nearest > training_stats['global_p95_nearest']
```

An outlier day means the atmosphere is in a configuration not well-represented by any of the k clusters — possibly a novel or transitional pattern.

```python
def classify_regime(z500_field: np.ndarray, doy: int,
                    pca, kmeans, regime_labels, clim,
                    training_stats: dict) -> dict:
    """
    z500_field: 2D numpy array (lat x lon) on the regime grid (2.5deg)
    doy: day of year (1-366)
    Returns dict with regime label, separation score, and outlier flag.
    """
    # Subtract climatology for this day-of-year
    clim_today = clim.sel(dayofyear=doy).values
    anomaly = z500_field - clim_today

    # Flatten and project
    x = anomaly.flatten().reshape(1, -1)
    x_reduced = pca.transform(x)

    # Find nearest cluster and distances to all centroids
    distances = kmeans.transform(x_reduced)[0]
    sorted_d = np.sort(distances)
    cluster_id = int(kmeans.predict(x_reduced)[0])

    # Separation: how distinct is the best match from the runner-up?
    d_nearest = sorted_d[0]
    d_second = sorted_d[1]
    separation = 1.0 - (d_nearest / d_second)

    # Outlier: is this day outside the training distribution?
    p95 = training_stats['global_p95_nearest']
    is_outlier = bool(d_nearest > p95)

    return {
        'regime_id': cluster_id,
        'regime_label': regime_labels[str(cluster_id)],
        'separation': round(float(separation), 3),
        'nearest_distance': round(float(d_nearest), 1),
        'is_outlier': is_outlier,
        'distances': [round(float(d), 1) for d in distances],
    }
```

### 3.3 Classify Today and Forecast Horizons

Both domains are always classified. The Z500 grids can be fetched once and reused across all models/horizons since the domain grids are fixed.

```python
FORECAST_HORIZONS = [24, 48, 72]
MODELS = ['gfs', 'ecmwf', 'icon']
DOMAINS = ['atlantic', 'mediterranean']

def classify_all(target_date: date):
    """
    Classify observed regime and per-model forecast regimes for both domains.
    Returns results for both domains; downstream consumers pick the domain
    with the higher separation score per airport.
    """
    results = {'date': target_date.isoformat(), 'domains': {}}

    for domain_name in DOMAINS:
        pca, kmeans, regime_labels, clim, stats = load_domain_artefacts(domain_name)
        domain_result = {}

        # T+0: observed regime (use GFS analysis or latest available)
        z500_analysis = fetch_openmeteo_z500_grid(domain_name, 'gfs', horizon_h=0)
        doy = target_date.timetuple().tm_yday
        domain_result['observed'] = classify_regime(
            z500_analysis, doy, pca, kmeans, regime_labels, clim, stats
        )

        # T+24/48/72: per-model forecasts
        domain_result['forecasts'] = {}
        for model in MODELS:
            domain_result['forecasts'][model] = {}
            for horizon_h in FORECAST_HORIZONS:
                z500_forecast = fetch_openmeteo_z500_grid(
                    domain_name, model, horizon_h=horizon_h
                )
                valid_date = target_date + timedelta(hours=horizon_h)
                valid_doy = valid_date.timetuple().tm_yday
                regime = classify_regime(
                    z500_forecast, valid_doy, pca, kmeans, regime_labels, clim, stats
                )
                domain_result['forecasts'][model][f't{horizon_h}'] = regime

        results['domains'][domain_name] = domain_result

    return results


def select_best_domain(results: dict, horizon_key: str = 'observed') -> dict:
    """
    Select the domain with the higher separation score for a given time step.
    Returns the winning domain's classification plus metadata about both.
    """
    atl = results['domains']['atlantic'][horizon_key]
    med = results['domains']['mediterranean'][horizon_key]

    winner = 'atlantic' if atl['separation'] >= med['separation'] else 'mediterranean'

    return {
        'selected_domain': winner,
        'regime_label': results['domains'][winner][horizon_key]['regime_label'],
        'separation': results['domains'][winner][horizon_key]['separation'],
        'atlantic': atl,
        'mediterranean': med,
    }
```

**Regime disagreement between models is itself a useful signal**: when models disagree on the large-scale regime, forecast uncertainty is higher regardless of which model is "best." Similarly, when the two domains produce different signals with similar separation scores, the large-scale pattern is ambiguous — which itself indicates higher forecast uncertainty.

---

## Part 4 — Verification Tagging and Model Skill Analysis

> **Naming**: this part was written before the verification subsystem existed and calls the
> target table `reconciliation`. **There is no such table.** The real one is
> **`verification_scores`** (`src/weatherbrief/db/models.py`, `VerificationScoreRow`) —
> model-vs-METAR records keyed by `(icao, observation_time, model, model_init_time, source)`.
> Translate before implementing:
>
> | This plan says | Actual column |
> |---|---|
> | `forecast_horizon_h` | `lead_hours` (also `days_out`) |
> | `predicted_ceiling` / `actual_ceiling` | `ceiling_delta_ft` (signed model − obs; no pair stored) |
> | `predicted_visibility` / `actual_visibility` | `visibility_delta_m` |
>
> So the skill queries in §4.2 become `AVG(ABS(ceiling_delta_ft))` etc., grouped by
> `model, regime_label, days_out`, and the `WHERE source = 'standalone'` filter matters —
> `source` mixes flight-driven and standalone-cycle rows.

### 4.1 Tag Verification Records

Each verification record should be tagged with the regime that was active on that day. This is the dataset that accumulates over time and enables model skill analysis.

Columns to add to `verification_scores`: `atlantic_regime VARCHAR(20)`, `atlantic_separation FLOAT`, `med_regime VARCHAR(20)`, `med_separation FLOAT`, `selected_domain VARCHAR(15)`. Both domains are always stored; the selected domain is recorded separately. Write the alembic migration with `batch_alter_table` (dev SQLite / prod MySQL — see CLAUDE.md).

**Retention landmine**: `verification_scores` is under the tiering rollout (#522/#527) — hot rows age out at 90 days into parquet artefacts. A regime tag that lives only on the hot table gives you a 90-day window, not the 3–6 months §4.2 assumes. Either carry the regime columns into the tiered export, or accumulate the skill dataset in its own rollup table.

**No backfill**: Open-Meteo historical API is not available (paid tier). Pre-deployment verification records will not have regime tags. The skill dataset starts accumulating from deployment date only.

### 4.2 Accumulating the Skill Dataset

After several months of tagged verification data, query model error by regime:

```python
import pandas as pd
import sqlite3

conn = sqlite3.connect('flyfun.db')

query = """
    SELECT
        model,
        selected_domain,
        CASE WHEN selected_domain = 'atlantic'
             THEN atlantic_regime ELSE med_regime END as regime_label,
        days_out,
        AVG(ABS(ceiling_delta_ft)) as mae_ceiling,
        AVG(ABS(visibility_delta_m)) as mae_visibility,
        COUNT(*) as n_samples
    FROM verification_scores
    WHERE source = 'standalone'
      AND CASE WHEN selected_domain = 'atlantic'
               THEN atlantic_separation ELSE med_separation END > 0.2
          -- exclude low-confidence regime days
    GROUP BY model, selected_domain, regime_label, days_out
    HAVING n_samples > 30             -- minimum sample size for reliability
    ORDER BY regime_label, days_out, mae_ceiling
"""

skill_df = pd.read_sql(query, conn)
```

### 4.3 Computing Model Weights

For each regime and horizon, compute a simple inverse-MAE weight for each model:

```python
def compute_model_weights(skill_df: pd.DataFrame,
                          regime: str,
                          days_out: int,
                          variable: str = 'mae_ceiling') -> dict:
    """
    Returns normalized weights for each model given regime and horizon.
    Falls back to equal weights if insufficient data.
    """
    subset = skill_df[
        (skill_df['regime_label'] == regime) &
        (skill_df['days_out'] == days_out)
    ].set_index('model')

    if len(subset) < 2 or subset['n_samples'].min() < 30:
        return {m: 1/3 for m in MODELS}

    mae_values = subset[variable]
    inverse_mae = 1.0 / mae_values
    weights = inverse_mae / inverse_mae.sum()

    return weights.to_dict()
```

### 4.4 Applying Weights to Forecast Blend

```python
def blend_forecasts(airport_forecasts: dict, weights: dict,
                    regime_label: str) -> dict:
    """
    airport_forecasts: {'gfs': {'ceiling': 1200, ...}, 'ecmwf': {...}, 'icon': {...}}
    weights: {'gfs': 0.28, 'ecmwf': 0.45, 'icon': 0.27}
    """
    blended = {}
    variables = airport_forecasts['gfs'].keys()

    for var in variables:
        blended[var] = sum(
            weights[model] * airport_forecasts[model][var]
            for model in MODELS
            if model in weights and model in airport_forecasts
        )

    blended['weights_used'] = weights
    blended['regime'] = regime_label
    return blended
```

---

## Part 5 — CLI Interface

The regime system is developed and tested independently of the main pipeline via a CLI. Only after validation is it wired into the daily cycle.

### 5.1 CLI Commands

```bash
# Phase 1 — ERA5 download (MacBook only, one-time)
python -m weatherbrief.regime download \
    --output-dir ~/Data/era5/ \
    --max-concurrent 12

# Phase 2 — Calibration (MacBook only, one-time)
python -m weatherbrief.regime calibrate \
    --input-dir ~/Data/era5/ \
    --output-dir ./regime_artefacts/

# Phase 3 — Classify (works on MacBook or prod)
python -m weatherbrief.regime classify \
    --date today \
    --artefacts-dir ./regime_artefacts/

python -m weatherbrief.regime classify \
    --date 2026-04-10 \
    --artefacts-dir ./regime_artefacts/
```

### 5.2 CLI Output Example

```
$ python -m weatherbrief.regime classify --date today

=== Atlantic Domain ===
Observed regime: NAO+ (separation: 0.62)

Forecast regimes:
         GFS        ECMWF      ICON
T+24     NAO+       NAO+       NAO+       AGREE
T+48     NAO+       blocking   NAO+       DISAGREE
T+72     blocking   blocking   NAO+       DISAGREE

=== Mediterranean Domain ===
Observed regime: zonal (separation: 0.45)

Forecast regimes:
         GFS        ECMWF      ICON
T+24     zonal      zonal      zonal      AGREE
T+48     zonal      zonal      anticycl.  DISAGREE
T+72     anticycl.  anticycl.  anticycl.  AGREE

=== Selected Domain (by separation) ===
Observed: atlantic (NAO+, 0.62 > 0.45)
```

---

## Part 6 — Implementation Sequence

### Phase 1 — ERA5 download (MacBook, 1-3 days of queue time)
- Register on Copernicus CDS, accept ERA5 licence
- Configure `~/.cdsapirc` with Personal Access Token
- Run `download` CLI — submits 360 monthly requests in parallel
- Verify all files downloaded, no truncation
- Back up to NAS

### Phase 2 — Calibration (MacBook, interactive)
- Run `calibrate` CLI for both domains
- Review centroid plots, manually assign regime labels
- Validate cluster frequencies and seasonal patterns
- Rsync artefacts to prod

### Phase 3 — Live classification CLI (MacBook, then prod)
- Implement Open-Meteo Z500 grid fetch wrapper
- Implement `classify` CLI using artefacts + Open-Meteo
- Test daily for a week, compare output against known weather patterns
- Verify separation scores and outlier detection behave sensibly

### Phase 4 — Pipeline integration (after CLI validation)
- Wire `classify` into daily cycle
- Add regime columns to `verification_scores` (batch_alter_table migration)
- Start accumulating tagged data

### Phase 5 — Model weighting (after 3-6 months of data)
- Query skill dataset by regime and horizon
- Compute inverse-MAE weights per regime x horizon x variable
- Apply blended weights in forecast output
- Monitor whether blended output improves on best single model

---

## Key Dependencies

| Library | Purpose | Where needed |
|---|---|---|
| `cdsapi >= 0.7.0` | ERA5 data download from Copernicus CDS | MacBook only (download phase) |
| `cfgrib` | Read GRIB files | MacBook only (calibration phase) |
| `xarray` | NetCDF handling for ERA5 and climatology | MacBook (calibration) + prod (classify) |
| `numpy` | Array operations, grid flattening | Everywhere |
| `scikit-learn` | PCA, KMeans, silhouette scoring | MacBook (calibration) + prod (classify) |
| `joblib` | Persisting fitted sklearn objects (version-aware) | MacBook (calibration) + prod (classify) |
| `matplotlib` + `cartopy` | Centroid map plotting for manual labelling | MacBook only (calibration) |
| Open-Meteo client (existing) | Live Z500 fetch | Prod (classify) |

**Prod-only dependencies**: `xarray`, `numpy`, `scikit-learn`, `joblib`. No `cdsapi`, `cfgrib`, `matplotlib`, or `cartopy` needed on prod.

---

## Important Caveats

**Open-Meteo vs ERA5 grid alignment**: the ERA5 climatology and PCA are trained on exact 2.5° grid points. Open-Meteo returns data at the requested coordinates, but may use different interpolation internally. In practice, the mismatch is small for 2.5° resolution (synoptic features span hundreds of km), but worth monitoring. If centroid distances from Open-Meteo data are systematically higher than from ERA5 training data, there's a bias. The training distance statistics (p95, median) provide the reference for detecting this.

**Cold start**: for the first 3-6 months after deployment, sample sizes per regime cell will be small. Use equal model weights until `n_samples > 30` per cell. The weighting system self-activates gradually as data accumulates.

**Regime persistence**: synoptic regimes persist for 5-15 days. The classifier operates on each day independently, so transitional days may oscillate between regimes. This is acceptable for initial deployment — the separation score will naturally be low on transitional days. If noisy oscillations contaminate the skill dataset, add a simple 3-day majority filter as a post-processing step.

**Seasonal signal strength**: the 4-cluster solution is trained on all 12 months. Regime patterns are strongest and most persistent in extended winter (ONDJFM) and weaker in summer. Summer days will tend to have lower separation scores. If regime-conditioned model weights show no skill improvement in summer, the system falls back to equal weights automatically via the n_samples guard — no special handling needed.

**Mediterranean domain limitations**: Atlantic-based regime classifications are less discriminating for Mediterranean weather. The separate Mediterranean classifier addresses this, but it has fewer grid points (153 vs 561) and the patterns may be less cleanly separated. If Mediterranean clusters show poor silhouette scores or seasonal artifacts during calibration, consider increasing k to 5 (adding Easterly/Sirocco) or restricting to extended winter.

**Sklearn version sensitivity**: `joblib`-serialized sklearn objects may not load across major version changes. The `domain.json` records the sklearn version used for fitting. If prod upgrades sklearn and loading fails, re-run calibration with the new version.

**Re-calibration schedule**: the PCA and k-means are fit on ERA5 1991-2020. Climate is non-stationary — regime frequencies and centroid positions may drift over decades. Plan to re-calibrate when ERA5 extends (e.g., 2001-2030) or if monitoring shows systematic drift in centroid distances over time.
