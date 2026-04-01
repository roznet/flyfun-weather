# METAR/TAF Accuracy System

> Automated collection of METAR/TAF observations during flights, compared against NWP model forecasts and TAFs, building a historical verification database for model accuracy analysis.

## Intent

We already have NWP model forecasts stored in briefing packs (GFS, ECMWF, ICON, etc.) and we fetch METAR/TAF on D-0 for the current observation comparison. But we don't systematically **archive** observations or **score** model accuracy over time.

This system:
1. **Collects** METAR/TAF observations during the flight window (1h before → flight end + 1h)
2. **Scores** each model's forecast against actual observations at multiple lead times (D-0 through D-7)
3. **Scores** TAF accuracy against METARs (TAF is also a forecast — was it right?)
4. **Archives** everything in a standalone, anonymized verification database

Over time this builds a dataset answering: "How accurate is GFS vs ECMWF at D-3 for ceiling in Alpine regions?"

## Core Design Principle: Observations Are Independent of Flights

The verification database is **not owned by any flight or user**. Observations are keyed by `(icao, observation_time)` — if three users fly through LFPG at 14:00, there's one observation row. A thin mapping table links flights to observations; when a user deletes their account, the mapping disappears but the verification data stays.

This makes the accuracy database a **growing, anonymized, community asset**.

## Architecture

```
scheduler.py (or standalone CLI)
├── run_verification_loop()        ← new asyncio loop, 10-min poll
│   ├── _find_verifiable_flights() ← flights in active window
│   ├── _collect_observations()    ← fetch METAR/TAF, dedup, store
│   └── _score_observations()     ← compare vs all packs/models
│
tasks/verification.py              ← core logic (testable independently)
├── collect_and_store()            ← fetch → dedup → insert observations
├── score_against_models()         ← load forecasts → compute deltas → insert scores
├── score_taf_against_metar()      ← TAF vs METAR accuracy
└── summarize_flight()             ← aggregate scores for one flight

models/verification.py             ← Pydantic models
├── VerificationObservation        ← METAR/TAF ground truth
├── VerificationScore              ← model-vs-reality comparison
├── TafVerificationScore           ← TAF-vs-METAR comparison
└── VerificationSummary            ← aggregated accuracy per model/lead

db/models.py                       ← SQLAlchemy tables (3 new)
├── VerificationObservationRow     ← standalone, keyed by (icao, observation_time)
├── VerificationScoreRow           ← standalone, keyed by (icao, time, model, init_time)
├── TafVerificationScoreRow        ← standalone, keyed by (icao, obs_time, taf_issue_time)
├── FlightVerificationMapRow       ← thin linkage, CASCADE on flight delete

CLI: python -m weatherbrief.verify  ← backfill, manual runs, export
```

## Database Schema

### `verification_observations` — Ground Truth Archive

```sql
CREATE TABLE verification_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    icao            VARCHAR(4)  NOT NULL,
    observation_time DATETIME   NOT NULL,     -- METAR's own timestamp (UTC)
    collected_at    DATETIME    NOT NULL,      -- when we fetched it

    -- METAR fields
    metar_raw           TEXT,
    flight_category     VARCHAR(4),           -- VFR/MVFR/IFR/LIFR
    ceiling_ft          INTEGER,
    visibility_m        INTEGER,
    wind_dir            INTEGER,
    wind_speed_kt       INTEGER,
    wind_gust_kt        INTEGER,
    temperature_c       INTEGER,
    dewpoint_c          INTEGER,
    qnh                 FLOAT,
    weather             TEXT,                  -- JSON list: ["RA","BR",...]

    -- TAF fields (active at observation_time)
    taf_raw             TEXT,
    taf_applicable      TEXT,                  -- applicable trend text at obs time
    taf_issue_time      DATETIME,              -- when the TAF was issued
    taf_flight_category VARCHAR(4),            -- TAF-predicted category at obs time
    taf_ceiling_ft      INTEGER,
    taf_visibility_m    INTEGER,
    taf_wind_dir        INTEGER,
    taf_wind_speed_kt   INTEGER,
    taf_wind_gust_kt    INTEGER,

    UNIQUE(icao, observation_time)
);

CREATE INDEX ix_verif_obs_icao ON verification_observations(icao);
CREATE INDEX ix_verif_obs_time ON verification_observations(observation_time);
```

### `verification_scores` — Model vs Reality

One row per (airport, observation, model, model_init_time). This lets us compare the same model at different lead times.

```sql
CREATE TABLE verification_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    icao                VARCHAR(4)  NOT NULL,
    observation_time    DATETIME    NOT NULL,
    model               VARCHAR(20) NOT NULL,  -- "gfs","ecmwf","icon","best_match"...
    model_init_time     DATETIME    NOT NULL,   -- NWP model run init time
    lead_hours          INTEGER     NOT NULL,   -- model_init → observation gap
    days_out            INTEGER     NOT NULL,   -- briefing pack days_out (D-0..D-7)

    -- Flight category comparison
    obs_flight_category     VARCHAR(4),
    model_flight_category   VARCHAR(4),
    category_match          BOOLEAN,

    -- Quantitative deltas (model - observation)
    ceiling_delta_ft        INTEGER,
    visibility_delta_m      FLOAT,
    wind_speed_delta_kt     FLOAT,
    wind_dir_delta_deg      FLOAT,    -- circular delta, -180..+180
    temperature_delta_c     FLOAT,

    -- Wind advisory comparison
    obs_wind_advisory       VARCHAR(10),       -- OK/CAUTION/WARNING
    model_wind_advisory     VARCHAR(10),
    advisory_match          BOOLEAN,

    -- Significant weather hit/miss
    obs_has_precipitation   BOOLEAN,
    model_has_precipitation BOOLEAN,
    obs_has_convection      BOOLEAN,           -- TS in METAR
    model_has_convection    BOOLEAN,            -- CAPE-based or model flag

    UNIQUE(icao, observation_time, model, model_init_time)
);

CREATE INDEX ix_verif_scores_model ON verification_scores(model, days_out);
CREATE INDEX ix_verif_scores_icao  ON verification_scores(icao);
CREATE INDEX ix_verif_scores_lead  ON verification_scores(lead_hours);
```

### `taf_verification_scores` — TAF vs METAR

TAFs are forecasts too. How accurate was the TAF compared to the actual METAR?

```sql
CREATE TABLE taf_verification_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    icao                VARCHAR(4)  NOT NULL,
    observation_time    DATETIME    NOT NULL,   -- METAR time (ground truth)
    taf_issue_time      DATETIME    NOT NULL,   -- when TAF was issued
    lead_hours          INTEGER     NOT NULL,   -- taf_issue → observation gap

    -- Flight category
    obs_flight_category VARCHAR(4),
    taf_flight_category VARCHAR(4),
    category_match      BOOLEAN,

    -- Deltas (TAF - observation)
    ceiling_delta_ft    INTEGER,
    visibility_delta_m  FLOAT,
    wind_speed_delta_kt FLOAT,
    wind_dir_delta_deg  FLOAT,

    UNIQUE(icao, observation_time, taf_issue_time)
);

CREATE INDEX ix_taf_verif_icao ON taf_verification_scores(icao);
```

### `flight_verification_map` — Thin Flight Linkage

```sql
CREATE TABLE flight_verification_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       VARCHAR(256) NOT NULL,      -- FK → flights ON DELETE CASCADE
    observation_id  INTEGER      NOT NULL,      -- FK → verification_observations
    distance_from_route_nm FLOAT,               -- how far airport is from route

    UNIQUE(flight_id, observation_id),
    FOREIGN KEY (flight_id) REFERENCES flights(id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES verification_observations(id)
);
```

### Flight-Level Tracking

Add column to `flights` table:

```sql
ALTER TABLE flights ADD COLUMN verification_status VARCHAR(16) DEFAULT NULL;
-- NULL = not eligible, "collecting" = in progress, "complete" = done
```

## Collection Loop

### Scheduler Integration

New loop in `scheduler.py`, alongside `run_scheduler_loop()` and `run_retention_loop()`:

```python
async def run_verification_loop(app_state) -> None:
    """Collect METAR/TAF observations for active flights."""
    logger.info("Verification loop started (poll every %ds)", _VERIF_POLL_SECONDS)
    await asyncio.sleep(_VERIF_STARTUP_DELAY)

    while True:
        try:
            await _process_verifications(app_state)
        except Exception:
            logger.error("Verification cycle failed", exc_info=True)
        await asyncio.sleep(_VERIF_POLL_SECONDS)
```

**Poll interval**: 10 minutes. METARs update every 30-60 minutes, SPECIs can be more frequent. 10 minutes gives good coverage without hammering aviationweather.gov.

### Finding Active Flights

```python
def _find_verifiable_flights(db: Session) -> list[FlightRow]:
    """Flights in the observation window: departure-1h to departure+duration+1h."""
    now = datetime.now(timezone.utc)
    
    rows = db.execute(
        select(FlightRow)
        .join(BriefingPackRow)  # must have at least one briefing
        .where(FlightRow.verification_status != "complete")
        .where(FlightRow.departure_time <= now + timedelta(hours=1))  # started or about to
        .distinct()
    ).scalars().all()

    active = []
    for row in rows:
        flight_end = row.departure_time + timedelta(hours=row.flight_duration_hours)
        window_end = flight_end + timedelta(hours=1)
        if now <= window_end:
            active.append(row)
    return active
```

### Collection Flow

```
For each verification cycle:

1. Find active flights → deduplicate airports across all flights
   (Airport X appears in 3 flights? Fetch METAR once)

2. Batch-fetch METAR/TAF for all unique airports
   (Reuse euro_aip.RouteWeatherService, corridor ~15nm)

3. For each observation:
   a. UPSERT into verification_observations (dedup on icao + observation_time)
   b. Parse TAF: find applicable trend at observation_time, extract fields
   c. Link to flights via flight_verification_map

4. For each new observation × each flight's briefing packs:
   a. Load forecasts.json for the pack
   b. Find route point closest to airport
   c. Find HourlyForecast closest to observation_time
   d. Compute deltas → INSERT into verification_scores
   e. Compute TAF vs METAR deltas → INSERT into taf_verification_scores

5. If now > flight_end + 1h:
   Mark flight verification_status = "complete"
```

### Corridor Width

**15nm** default (tighter than the 30nm briefing corridor). This balances:
- Enough airports for meaningful coverage
- Not so many that we store noise from distant fields
- Configurable via env var `VERIFICATION_CORRIDOR_NM`

## Scoring Logic

### Model → METAR Comparison

Reuse and refactor logic from `tasks/route_weather.py:run_observation_comparison()`:

```python
def compute_verification_score(
    obs: VerificationObservation,
    forecast: HourlyForecast,
    model: str,
    model_init_time: datetime,
    days_out: int,
) -> VerificationScore:
    """Compare one model forecast against one METAR observation."""
    
    lead_hours = int((obs.observation_time - model_init_time).total_seconds() / 3600)
    
    # Flight category from model (reuse classify_flight_category)
    model_cat = classify_flight_category(
        ceiling_ft=model_ceiling_from_sounding(forecast),  # or from route analysis
        visibility_m=forecast.visibility_m,
    )
    
    return VerificationScore(
        icao=obs.icao,
        observation_time=obs.observation_time,
        model=model,
        model_init_time=model_init_time,
        lead_hours=lead_hours,
        days_out=days_out,
        obs_flight_category=obs.flight_category,
        model_flight_category=model_cat,
        category_match=(obs.flight_category == model_cat),
        ceiling_delta_ft=_delta(model_ceiling, obs.ceiling_ft),
        visibility_delta_m=_delta(forecast.visibility_m, obs.visibility_m),
        wind_speed_delta_kt=_delta(forecast.wind_speed_10m_kt, obs.wind_speed_kt),
        wind_dir_delta_deg=_circular_delta(forecast.wind_direction_10m_deg, obs.wind_dir),
        temperature_delta_c=_delta(forecast.temperature_2m_c, obs.temperature_c),
        ...
    )
```

### TAF → METAR Comparison

```python
def compute_taf_verification(
    obs: VerificationObservation,
) -> TafVerificationScore | None:
    """Compare TAF prediction against actual METAR for same airport/time."""
    if not obs.taf_flight_category:
        return None
    
    lead_hours = int((obs.observation_time - obs.taf_issue_time).total_seconds() / 3600)
    
    return TafVerificationScore(
        icao=obs.icao,
        observation_time=obs.observation_time,
        taf_issue_time=obs.taf_issue_time,
        lead_hours=lead_hours,
        obs_flight_category=obs.flight_category,
        taf_flight_category=obs.taf_flight_category,
        category_match=(obs.flight_category == obs.taf_flight_category),
        ceiling_delta_ft=_delta(obs.taf_ceiling_ft, obs.ceiling_ft),
        ...
    )
```

### Deriving Model Ceiling

Challenge: NWP models don't directly output METAR-style ceiling. Two approaches:

1. **Sounding-derived ceiling** (preferred): Use `analyze_sounding()` → cloud layers → lowest cloud base. Already computed in route analyses — stored in `route_analyses.json`.
2. **Cloud cover heuristic**: If sounding analysis not available, use `cloud_cover_pct > 50%` at each pressure level → convert to altitude.

For verification, we should recompute sounding analysis at the observation time, not just reuse the briefing's analysis (which targets the departure window). This means the scoring step is slightly heavier — but it gives accurate ceiling estimates.

**Pragmatic v1**: Use the nearest-hour sounding from the briefing pack's `forecasts.json`. The hourly resolution (1h) is close enough to METAR times for verification purposes.

## CLI Interface

```bash
# Manual collection for a specific flight
python -m weatherbrief.verify collect --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Score observations against all packs for a flight  
python -m weatherbrief.verify score --flight-id "LFPG-EDDF-2026-04-01-abc123"

# Backfill: re-process past flights (fetch historical METARs if available)
python -m weatherbrief.verify backfill --since 2026-01-01

# Export accuracy data
python -m weatherbrief.verify export --format csv --output accuracy.csv
python -m weatherbrief.verify export --format json --output accuracy.json

# Summary statistics
python -m weatherbrief.verify stats
python -m weatherbrief.verify stats --model gfs --days-out 1
python -m weatherbrief.verify stats --icao LFPG
```

## Data Volume

Estimates at current scale (350 briefings/month, growing):

| Table | Rows/month | Row size | Monthly size |
|-------|-----------|----------|-------------|
| `verification_observations` | ~3,000 | ~500B | ~1.5 MB |
| `verification_scores` | ~50,000 | ~200B | ~10 MB |
| `taf_verification_scores` | ~3,000 | ~150B | ~450 KB |
| `flight_verification_map` | ~10,000 | ~50B | ~500 KB |

**Calculation**: ~350 flights × ~8 airports × ~6 METAR fetches/flight = ~17K observations, but deduplicated across flights sharing airports → ~3K unique. Scores: 3K obs × ~6 models × ~3 packs = ~50K.

At 10× growth (3,500 flights/month): ~120 MB/month → ~1.4 GB/year. Trivial for MySQL.

## Accuracy Metrics & Queries

### Per-Model Accuracy at Lead Time

```sql
SELECT model, days_out,
       COUNT(*) as n,
       AVG(category_match) as category_accuracy,
       AVG(ABS(ceiling_delta_ft)) as ceiling_mae_ft,
       AVG(ceiling_delta_ft) as ceiling_bias_ft,
       AVG(ABS(wind_speed_delta_kt)) as wind_mae_kt,
       AVG(ABS(temperature_delta_c)) as temp_mae_c
FROM verification_scores
GROUP BY model, days_out
ORDER BY model, days_out;
```

### TAF vs Model Accuracy

```sql
-- Which is more accurate for ceiling: TAF or GFS at D-0?
SELECT 'TAF' as source,
       AVG(category_match) as cat_accuracy,
       AVG(ABS(ceiling_delta_ft)) as ceiling_mae
FROM taf_verification_scores
UNION ALL
SELECT 'GFS',
       AVG(category_match),
       AVG(ABS(ceiling_delta_ft))
FROM verification_scores
WHERE model = 'gfs' AND days_out = 0;
```

### Regional Accuracy (by ICAO prefix)

```sql
SELECT SUBSTR(icao, 1, 2) as region,
       model,
       AVG(category_match) as accuracy
FROM verification_scores
WHERE days_out = 1
GROUP BY region, model
ORDER BY region, accuracy DESC;
```

### Seasonal Accuracy

```sql
SELECT model,
       CASE
         WHEN MONTH(observation_time) IN (12,1,2) THEN 'winter'
         WHEN MONTH(observation_time) IN (6,7,8) THEN 'summer'
         ELSE 'shoulder'
       END as season,
       AVG(category_match) as accuracy
FROM verification_scores
WHERE days_out <= 1
GROUP BY model, season;
```

### IFR Accuracy (when it matters most)

```sql
SELECT model, days_out,
       AVG(category_match) as accuracy,
       COUNT(*) as n_ifr_obs
FROM verification_scores
WHERE obs_flight_category IN ('IFR', 'LIFR')
GROUP BY model, days_out;
```

## Key Design Choices

| Decision | Rationale |
|----------|-----------|
| Observations independent of flights | Dedup across flights, survives account deletion, anonymized community data |
| Thin mapping table with CASCADE | Flight deletion removes linkage, not data |
| UNIQUE(icao, observation_time) | Natural dedup key — same METAR never stored twice |
| TAF verification as separate table | Different key structure (taf_issue_time), different semantics |
| 15nm corridor (not 30nm) | Tighter = fewer noisy small airfields, still catches alternates |
| 10-min poll interval | METARs update ~30-60min, SPECIs more frequent; good coverage vs API load |
| Scoring deferred from collection | Collection is time-critical (METARs age out), scoring can be batched |
| CLI for backfill/export | Can run independently of web process, enables data science workflows |
| Store all fields, not just deltas | Raw observations enable future metrics we haven't thought of yet |
| Sounding-derived model ceiling | Consistent with briefing's ceiling method; nearest-hour is acceptable |

## Implementation Phases

### Phase 1: Collection Infrastructure
- DB tables + Alembic migration
- `tasks/verification.py`: `collect_and_store()`
- Scheduler loop integration
- CLI: `verify collect`, `verify export`
- No scoring yet — just archive observations

### Phase 2: Model Scoring
- `score_against_models()` — load forecasts, compute deltas
- TAF scoring
- CLI: `verify score`, `verify stats`
- Backfill past flights where forecasts.json still exists

### Phase 3: Surfacing
- API endpoints: `/api/verification/stats`, `/api/verification/flight/{id}`
- Dashboard page: model accuracy charts
- Per-flight verification report
- Confidence annotations on forecasts ("historically X% accurate at D-3")

## Open Considerations

- **Historical METAR sources**: For backfill, aviationweather.gov has limited history. Iowa State Mesonet or OGIMET could provide deeper archives if needed.
- **Model ceiling derivation**: Sounding analysis is the most accurate but heaviest approach. Could cache sounding results during scoring or accept nearest-hour approximation.
- **SPECI handling**: Special METARs (significant weather changes) are more interesting for verification but occur irregularly. The 10-min poll should catch most of them.
- **Retention**: Keep forever. This is the whole point — a growing accuracy dataset. At current scale, storage is negligible.

## References

- Existing METAR/TAF: `src/weatherbrief/tasks/route_weather.py`, `src/weatherbrief/models/observations.py`
- Flight category logic: `src/weatherbrief/analysis/airport_conditions.py`
- Sounding analysis: `src/weatherbrief/analysis/sounding/`
- Scheduler: `src/weatherbrief/scheduler.py`
- DB models: `src/weatherbrief/db/models.py`
- Design: `designs/metar-taf-route-weather.md`
