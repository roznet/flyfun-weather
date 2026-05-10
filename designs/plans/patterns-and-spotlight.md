# Patterns & Spotlight — climatology rework of the Forecast tab

> Replace the "Model Accuracy" map and "Accuracy Stats" dashboard with two
> climatology-first views (Patterns + Spotlight) backed by higher-cadence
> METAR ingest and pre-aggregated monthly/daily summaries. Reframes the
> question from "how good are the models?" (boring — they're all 80–85%)
> to "what is this airport actually like, and where do models trip?"

## Status (2026-05-10)

- ✅ **Phase 1** — METAR ingest decoupling + 30-min cadence. Shipped (PR #140) and deployed to weather.flyfun.aero.
- 🟡 **Phase 2** — `airport_monthly_summary` rollup. In progress (this branch).
- 🟡 **Phase 3** — `airport_daily_summary` rollup. In progress (this branch, alongside Phase 2).
- ⏸ **Phase 4** — MySQL partitioning. **Deferred** — InnoDB rejects partitioning on tables with FKs (parent or child), and `verification_observations` has 3 inbound CASCADE FKs from score / map tables. Decided 2026-05-10 to use plain `DELETE` for retention (Option B): at current scale (~80–160k rows/month) indexed-range deletes complete in seconds; revisit only if perf becomes a real problem. ``ensure_future_partitions()`` left as a no-op stub for future pivot.
- ⏸ Phases 5–6 not yet started.

## Why

The two existing tabs under `/maps` are spatially correct but narratively
weak:

- **Model Accuracy map** — every airport is some shade of green (80–85%
  category match). ECMWF marginally better than ICON marginally better
  than GFS. No surprises, no actionable insight.
- **Accuracy Stats** — embedded `verification.html` iframe of the admin
  dashboard. Useful for calibration, dull for users.

Meanwhile `verification_observations` already holds ~830 European
airports' worth of hourly METARs with weather phenomena, ceilings, vis,
winds, TAFs — the raw material for **climatology** (IR usefulness,
foggiest fields, stormiest months) and **directional trust** (where
models said VFR but METAR was IFR — the *dangerous* misses).

The rework reuses every existing table; it adds rollup tables for query
speed, splits METAR ingest from scoring so we can sample more often, and
reskins the two tabs around questions pilots actually want answered.

## What we're building

| Tab (was) | Tab (new) | Form factor | Headline content |
|---|---|---|---|
| Forecast | Forecast | unchanged | unchanged |
| Model Accuracy | **Patterns** | Leaflet map | IR usefulness, foggiest, stormiest, windiest; per-model trust modes (optimistic-bust, TS miss/FAR) as alternate metrics |
| Accuracy Stats | **Spotlight** | Rankings + drill-down | Top-N leaderboards, per-airport detail panel (calendar heatmap, diurnal strip, TAF-vs-models showdown, notable busts) |

Both tabs deep-link via URL state (existing pattern from PR #117) and
gain a **month dropdown** so any past month can be inspected.

Supporting infra:

1. **30-min METAR ingest loop** decoupled from verification scoring.
2. **`airport_monthly_summary`** rollup (one row per `month × icao`).
3. **`airport_daily_summary`** rollup (one row per `date × icao`).
4. **180-day raw retention** for `verification_observations` /
   `verification_scores` / `taf_verification_scores`, dropped after
   rollup confirmed.
5. **MySQL monthly partitioning** on `verification_observations` so
   retention is `ALTER TABLE … DROP PARTITION` (instant, no row scan).

## Decided choices

- 30-min METAR cadence; 30-min dedup bucket (relax from "one per clock
  hour" to "one per 30-min bucket")
- 180-day raw obs/score retention while we tune rollup methodology
- Add daily summary now (cheap, can't be backfilled later)
- Partition `verification_observations` now while volume is small
- Tab names: **Patterns** + **Spotlight**
- Table names: `airport_monthly_summary` + `airport_daily_summary`
  (engineering-neutral; decoupled from the user-facing tab name)
- **Patterns default landing**: IR Usefulness, last 30 days
- **Spotlight default landing**: leaderboards + a "featured airport"
  card (auto-curated, e.g. biggest bust in the last 24h)
- **Auth**: Patterns is **public** (climatology = SEO/discovery value);
  Spotlight is **auth-only** (drill-down + briefing-pack links)
- **Admin dashboard fate**: existing `verification.html` is kept at
  `/admin/verification.html`, admin-only. Calibration-specific stats
  (cycle timings, error rates, raw MAE trends) live there; Spotlight
  is the user-facing surface.

## Phases

The phases are layered: 1–3 are pure infra and ship independently; 4–5
are user-facing and depend on rollup tables existing (but can render
against the live `verification_observations` table on day one if we want
to ship them before the rollup backfill completes).

- **Phase 1** — Decouple METAR ingest, run every 30 min ✅ shipped
- **Phase 2** — `airport_monthly_summary` rollup + retention task
- **Phase 3** — `airport_daily_summary` rollup
- **Phase 4** — ~~MySQL partitioning of `verification_observations`~~ **deferred** — see Status block. Use plain DELETE for retention.
- **Phase 5** — Patterns tab (replaces Model Accuracy map)
- **Phase 6** — Spotlight tab (replaces Accuracy Stats)

Phases 1–3 are independent and can run in parallel. Phases 5–6 depend
on phase 2 (monthly summary) at minimum; phase 6's calendar heatmap
depends on phase 3 (daily summary).

---

## Phase 1 — Decouple METAR ingest, 30-min cadence

### Goal

Split the standalone pipeline into three independent loops:

| Loop | Cadence | Job |
|---|---|---|
| METAR ingest (new) | every 30 min | Fetch METARs for the watchlist, upsert into `verification_observations`. No scoring, no forecast fetch. |
| Forecast fetch (existing) | 07/19 UTC | Unchanged — Open-Meteo per model, snapshot enrichment. |
| Verification scoring (existing) | 06/09/12/15/18 UTC | Unchanged shape, but stops fetching METARs itself. Reads the nearest stored obs per `(icao, scoring_hour)` from the table. |

### Files

- `src/weatherbrief/scheduler.py` — add `run_metar_ingest_loop`. Existing
  `run_standalone_verification_loop` stays but its METAR-fetch phase is
  removed.
- `src/weatherbrief/tasks/standalone_verification.py` — extract the METAR
  fetch logic from `run_standalone_cycle` into a new
  `run_metar_ingest_cycle()` function. The existing scoring path picks
  the nearest stored obs by `(icao, observation_time)` rather than
  fetching fresh.
- `src/weatherbrief/tasks/verification.py` — confirm flight-based
  collection is unaffected (it has its own 10-min poll and is more
  bursty during active flights).

### Approach

1. Factor METAR fetch out of `run_standalone_cycle` into
   `run_metar_ingest_cycle(db, watchlist)`. It does Phase C only:
   fetch METAR/TAF for ~830 airports, upsert observations, no scoring.
2. Loosen the dedup filter from "one obs per (icao, clock hour), keep
   closest to top of hour" to **"one obs per (icao, 30-min bucket),
   keep closest to bucket center."** The `UNIQUE(icao, observation_time)`
   constraint already handles exact-duplicate protection. SPECIs at
   off-cycle times (e.g. for sudden TS) get captured.
3. Add a new scheduler loop that fires every 30 min (e.g. at HH:05 and
   HH:35 UTC, offset slightly from METAR issuance times of HH:00/HH:30
   to give airports time to publish).
4. In `run_standalone_cycle`, when `score_observations=True`, replace
   the METAR fetch with a query: for each watchlist airport, find the
   most recent observation within 60 min of the cycle's nominal hour.
   Existing scoring against the snapshot then proceeds as before.
5. Wire the new loop into `weatherbrief.api.app:lifespan` next to the
   existing standalone loops; respect a `DISABLE_METAR_INGEST=1` env
   override.

### Expected impact

- ~2× obs volume (~80k → ~160k rows/month) — see Phase 4 for partitioning.
- Better diurnal resolution for Patterns/Spotlight (ten samples per day
  instead of five).
- SPECI capture for "stormiest"/"most volatile" leaderboards.
- Verification scoring becomes a pure-DB operation (no aviationweather.gov
  call), so it's faster and more reliable.

### Acceptance criteria

- New loop visible in `verification_cycles` with `source='metar_ingest'`.
- `run_standalone_cycle(score_observations=True)` no longer hits
  aviationweather.gov; `verification_cycles.fetch_ms` for those rows is 0.
- `verification_observations` row counts grow at roughly 2× prior rate
  (sanity check, not exact — depends on which airports issue SPECIs).
- Existing verification tests pass; flight-based collection unchanged.

### Risk notes

- aviationweather.gov rate limits — 48 fetches/day × ~830 airports
  chunked at 400/batch = ~96 calls/day. Well under any reasonable limit
  but worth confirming we're not flagged.
- The 30-min dedup loosening is a behavior change for *existing*
  observations. New rows will land at :00 and :30; rows already in the
  table from the old "top of hour" filter are kept as-is. No backfill
  needed.

---

## Phase 2 — `airport_monthly_summary` rollup

### Goal

Pre-aggregate observations per `(month, icao)` so leaderboard and map
queries hit ~830 rows per month instead of scanning ~160k raw obs.

### Files

- `src/weatherbrief/db/models.py` — add `AirportMonthlySummaryRow`.
- `alembic/versions/053_airport_summary_tables.py` — new migration
  (combine with Phase 3's daily table in one migration; both are pure
  `op.create_table` so they work on SQLite + MySQL without batch mode).
- `src/weatherbrief/tasks/airport_summary.py` — new module with
  `rollup_month(month, db)` and `rollup_all_complete_months(db)`.
  Idempotent (DELETE + INSERT per month), parallels
  `verification_rollup.rollup_month`.
- `src/weatherbrief/tasks/retention.py` — new module with
  `prune_raw_observations(retain_days=180, db)`. Called after rollup
  confirms the month is summarised.
- `src/weatherbrief/scheduler.py` — call `rollup_all_complete_months`
  + `prune_raw_observations` on a daily cadence (e.g. 02:00 UTC).

### Schema

```python
class AirportMonthlySummaryRow(Base):
    __tablename__ = "airport_monthly_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    month = Column(DateTime, nullable=False)        # first day of month UTC
    icao = Column(String(4), nullable=False)
    n_obs = Column(Integer, nullable=False)

    # Category counts (drives IR usefulness, VFR rate)
    n_vfr = Column(Integer, default=0)
    n_mvfr = Column(Integer, default=0)
    n_ifr = Column(Integer, default=0)
    n_lifr = Column(Integer, default=0)

    # Phenomena counts (drives foggiest, stormiest, wettest)
    n_fog = Column(Integer, default=0)              # BR or FG in weather list
    n_ts = Column(Integer, default=0)               # TS in weather list
    n_precip = Column(Integer, default=0)           # any precip phenomenon
    n_snow = Column(Integer, default=0)

    # Continuous percentiles / extremes
    ceiling_p10_ft = Column(Integer)
    ceiling_p50_ft = Column(Integer)
    ceiling_p90_ft = Column(Integer)
    visibility_p10_m = Column(Integer)
    visibility_p50_m = Column(Integer)
    wind_mean_kt = Column(Float)
    wind_p95_kt = Column(Float)
    gust_max_kt = Column(Integer)
    temp_min_c = Column(Float)
    temp_max_c = Column(Float)

    # Volatility — count of consecutive-obs category transitions
    category_changes = Column(Integer, default=0)

    # Diurnal bucket (per hour-of-day): {"00": {"n": .., "n_ifr": ..}, ...}
    diurnal_json = Column(Text)

    __table_args__ = (
        UniqueConstraint("month", "icao", name="uq_airport_monthly_summary"),
        Index("ix_ams_month", "month"),
        Index("ix_ams_icao", "icao"),
    )
```

### Approach

1. `rollup_month(month, db)` reads all `verification_observations` where
   `observation_time` falls in `month`, groups by `icao`, computes the
   aggregates above, DELETE existing rows for that month, INSERT new
   rows. Idempotent.
2. `rollup_all_complete_months(db)` finds the MAX month already
   summarised, walks forward through completed months (current UTC past
   month end), calling `rollup_month` for each.
3. `prune_raw_observations(retain_days=180)`:
   - Compute cutoff = now() - 180 days
   - For each month strictly older than `cutoff`, verify a row exists
     in `airport_monthly_summary` for at least one airport in that
     month (sanity — never delete unsummarised data)
   - DELETE FROM `verification_observations` WHERE `observation_time` <
     cutoff
   - Same for `verification_scores`, `taf_verification_scores`.
4. Daily scheduler tick at 02:00 UTC runs both. Cheap; rollup of one
   completed month is a few seconds.

### Expected impact

- Leaderboard queries (most VFR, foggiest, stormiest …) become single
  table reads against ~830 rows for the selected month.
- Patterns map for any past month: 830-row response, instant.
- Hot table size bounded at ~6 months × ~160k = ~1M obs rows steady
  state. Easily handled by MySQL even unpartitioned, but Phase 4
  partitioning makes the DROP free.

### Acceptance criteria

- `airport_monthly_summary` populated for every completed month since
  the standalone pipeline started. Initial backfill via CLI.
- Re-running rollup produces identical row counts (idempotency).
- After retention runs, `verification_observations` MIN(observation_time)
  is within 180 days of now.
- New CLI: `python -m weatherbrief.verify rollup-summary --month
  2026-04` and `--all`.

### Risk notes

- `category_changes` requires ordering observations within each
  `(icao, day)` then counting transitions. Straightforward in pandas
  but a non-trivial SQL window function — likely cleanest to compute in
  Python after fetching the obs for the month.
- Diurnal JSON: keep keys as zero-padded HH strings for stable sorting.
  Serialise compact (no whitespace) — at 24 hours × ~5 numeric fields,
  rows stay well under 1KB.

---

## Phase 3 — `airport_daily_summary` rollup

### Goal

Daily granularity for the Spotlight calendar heatmap and "worst day of
the month" leaderboards. Same shape as monthly, simpler aggregates.

### Files

- `src/weatherbrief/db/models.py` — add `AirportDailySummaryRow`.
- `alembic/versions/053_airport_summary_tables.py` — same migration as
  Phase 2.
- `src/weatherbrief/tasks/airport_summary.py` — extend with
  `rollup_day(date, db)` and `rollup_all_complete_days(db)`.

### Schema

```python
class AirportDailySummaryRow(Base):
    __tablename__ = "airport_daily_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    icao = Column(String(4), nullable=False)
    n_obs = Column(Integer, nullable=False)

    worst_category = Column(String(4))              # VFR/MVFR/IFR/LIFR
    n_vfr = Column(Integer, default=0)
    n_mvfr = Column(Integer, default=0)
    n_ifr = Column(Integer, default=0)
    n_lifr = Column(Integer, default=0)

    n_fog = Column(Integer, default=0)
    n_ts = Column(Integer, default=0)
    n_precip = Column(Integer, default=0)

    ceiling_min_ft = Column(Integer)
    ceiling_p10_ft = Column(Integer)
    visibility_min_m = Column(Integer)
    wind_max_kt = Column(Integer)
    gust_max_kt = Column(Integer)
    temp_min_c = Column(Float)
    temp_max_c = Column(Float)

    __table_args__ = (
        UniqueConstraint("date", "icao", name="uq_airport_daily_summary"),
        Index("ix_ads_date", "date"),
        Index("ix_ads_icao", "icao"),
    )
```

### Approach

Same pattern as monthly. `rollup_day(date)` reads obs for that UTC day,
groups by icao, computes aggregates, DELETE+INSERT.
`rollup_all_complete_days` walks forward from MAX(date).

Run from the same 02:00 UTC tick; rolling up one completed day is sub-second.

### Expected impact

- ~830 rows/day → ~25k/month → ~300k/year. Trivial.
- Calendar heatmaps work for any past month, including months whose
  raw obs have aged out (post-180-day retention).
- "Worst day of last month" / "longest IFR streak" leaderboards become
  cheap window-function queries on ~25k rows.

### Acceptance criteria

- Backfill produces one row per (date, icao) for every airport-day with
  at least one observation since standalone pipeline start.
- Idempotent — re-running for a date produces same row.
- Calendar heatmap query (Phase 6) returns 30 rows in <50ms.

### Risk notes

- Date in UTC vs local time: standardise on UTC `date` derived from
  `observation_time`. Calendar heatmap labels can be presented as UTC
  to match the rest of the system.

---

## Phase 4 — MySQL partition `verification_observations`

### Goal

Make 180-day retention an `ALTER TABLE … DROP PARTITION` (instant, no
row scan) instead of a `DELETE FROM` (full scan, fragments table).
Cheap to set up now; painful to retrofit when the table is large.

### Files

- `alembic/versions/054_partition_verification_observations.py` — MySQL
  only, SQLite no-op (use `op.get_bind().dialect.name == "mysql"` guard
  per the CLAUDE.md note).

### Approach

```python
# upgrade — MySQL only
if op.get_bind().dialect.name == "mysql":
    op.execute("""
        ALTER TABLE verification_observations
        PARTITION BY RANGE (TO_DAYS(observation_time)) (
            PARTITION p_pre_202601 VALUES LESS THAN (TO_DAYS('2026-01-01')),
            PARTITION p_202601 VALUES LESS THAN (TO_DAYS('2026-02-01')),
            ...
            PARTITION p_future VALUES LESS THAN MAXVALUE
        )
    """)
```

Then add a small task in `retention.py`:

```python
def ensure_future_partitions(months_ahead=3):
    """Pre-create partitions so inserts never fail."""
```

Called from the daily 02:00 UTC tick.

### Expected impact

- Retention drop becomes O(1) regardless of table size.
- Partition pruning on date-range queries (Patterns map for a past
  month) becomes automatic.
- No application code change — partitioning is transparent.

### Acceptance criteria

- Migration applies cleanly to a fresh MySQL DB and to the production
  DB (test on staging snapshot first).
- SQLite dev DB unaffected.
- `SHOW CREATE TABLE verification_observations` shows partition
  definitions on MySQL.
- After retention runs in test, `SHOW TABLE STATUS` confirms partition
  count decreased (oldest partition dropped).

### Risk notes

- Partitioning a populated table on MySQL rewrites it. On the
  production-sized table (~few hundred MB at most right now) this is a
  minutes-scale operation; do during low-traffic window.
- `verification_scores` and `taf_verification_scores` could also be
  partitioned — defer unless growth analysis suggests need. Their
  retention drop via plain DELETE is acceptable at current volume.

---

## Phase 5 — Patterns tab

### Goal

Replace the "Model Accuracy" tab with a Leaflet map whose default lens
is climatology, with model-trust metrics as alternate views.

### Files

- `web/maps.html` — rename tab label, restructure controls.
- `web/ts/maps-main.ts` — controller for new metric picker
  (grouped: Patterns / Trust), month dropdown, model selector
  (only relevant in Trust modes).
- `web/ts/adapters/maps-adapter.ts` — new `fetchPatternsMap()` calling
  a new endpoint.
- `web/ts/visualization/weather-map.ts` — extend `setForecastData` /
  `setVerificationData` with `setPatternsData` (or generalise).
- `web/ts/utils/url-state.ts` — add Patterns tab params (`metric`,
  `month`, `model`) to the encoder/decoder.
- `src/weatherbrief/api/maps.py` — add `GET /maps/patterns?month=…&metric=…&model=…`.
- `src/weatherbrief/tasks/map_queries.py` — add
  `get_patterns_map_data(month, metric, model)`.
- `src/weatherbrief/tasks/cache_builder.py` — extend
  `rebuild_all` with patterns cache keys
  (`patterns_map:{month}:{metric}:{model}`); use the existing
  `verification_cache` table.

### Metrics

Picker grouped:

**Patterns (observation-only, no model)**
- IR Usefulness — % hours IFR/LIFR
- VFR Reliability — % VFR hours
- Foggiest — % hours with BR/FG
- Stormiest — TS hour count
- Windiest — p95 wind / max gust
- Most Volatile — `category_changes` per day

**Trust (per-model)**
- Optimistic Busts — % hours model=VFR but METAR=IFR/LIFR
- Pessimistic Busts — opposite
- TS Miss Rate / TS False-Alarm Rate
- Ceiling MAE (the existing accuracy view, kept as an option)

Period control = **month dropdown** populated from
`SELECT DISTINCT month FROM airport_monthly_summary ORDER BY month DESC`,
plus a "Last 30 days" pseudo-month that queries live.

### Approach

1. Default landing view: **IR Usefulness, last 30 days, no model
   selected.** Sets the tone that this is climatology-first. (Decided.)
2. Tab is **public** (no auth required) — climatology data is harmless
   and the page has SEO value. Cache layer must serve unauthenticated
   requests.
3. Patterns metrics → query `airport_monthly_summary` for the selected
   month (or live obs for "Last 30 days"). Returns one row per icao
   with the relevant numbers; client colours markers.
4. Trust metrics → query `verification_monthly_stats` filtered by
   `source='standalone'` and `(month, model, days_out)`. Same one-row-
   per-icao response shape so the map renderer is uniform.
5. Cache layer reuses `verification_cache`. New keys
   `patterns_map:{month}:{metric}:{model_or_NA}` rebuilt from
   `cache_builder.rebuild_all` after METAR ingest cycles (for "Last
   30 days") and after rollup (for completed months).
6. Click an airport → drill to Spotlight tab with that airport
   pre-selected (URL state handover). Since Spotlight is auth-only,
   unauthenticated users get an auth prompt at this point.

### Acceptance criteria

- Tab renders in <500ms for "Last 30 days, IR Usefulness, all airports."
- Switching month / metric / model all encode in URL via existing
  `url-state.ts` round-trip.
- "Last 30 days" view stays fresh (cache invalidates on each METAR
  ingest cycle).
- Past-month views are stable and instant (cache hit on
  `airport_monthly_summary`).
- Click-through to Spotlight passes the airport ICAO + selected month
  via URL.

### Risk notes

- Color scales need calibration — IR usefulness varies wildly (5% at
  EGLL, 35% at LFRB). Use a fixed 0–40% scale rather than auto-fit so
  cross-airport comparison is meaningful.
- "Most Volatile" is the noisiest metric; consider a minimum
  observation count threshold (n_obs >= 50) before ranking.

---

## Phase 6 — Spotlight tab

### Goal

Replace the embedded `verification.html` iframe with a leaderboards +
per-airport drill-down page.

### Files

- `web/maps.html` — Spotlight tab pane (replace iframe).
- `web/ts/spotlight-main.ts` — new controller.
- `web/ts/visualization/calendar-heatmap.ts` — new component (30 cells,
  one per day, colored by worst_category).
- `web/ts/visualization/diurnal-strip.ts` — new component (24 cells per
  hour-of-day, colored by % IFR).
- `web/ts/adapters/spotlight-adapter.ts` — API client.
- `src/weatherbrief/api/spotlight.py` — new module:
  - `GET /spotlight/leaderboards?month=…`
  - `GET /spotlight/featured` (default landing card)
  - `GET /spotlight/airport/{icao}?month=…`
- `src/weatherbrief/tasks/spotlight_queries.py` — new module with
  `get_leaderboards(month)`, `get_featured_airport()`,
  `get_airport_detail(icao, month)`.

### Layout

Tab is **auth-only** (status quo for `/maps` drill-downs and briefing
links).

**Default landing (no airport selected)**: leaderboards + a single
**featured airport card** at the top, auto-curated server-side. Default
heuristic: the airport with the largest single-hour optimistic bust in
the last 24 hours (joins `verification_scores` to
`verification_observations`, picks the row with the worst category-
direction × ceiling delta). Falls back to "biggest TS surprise of the
last 7 days" if no recent bust is severe enough. The card shows the
one-line bust description + a "view this airport" CTA into the detail
panel below.

**Top — Leaderboards** (5–6 compact cards, all driven by
`airport_monthly_summary` for selected month):

- Most VFR / Most IFR
- Foggiest / Stormiest / Windiest
- Most Deceptive *(highest optimistic-bust rate per selected model;
  pulls from `verification_monthly_stats`)*
- Worst Single Bust *(joins
  `verification_scores` to `verification_observations` for the largest
  category drop in the month)*
- TAF vs Model Champion *(per-airport `category_match` winners across
  TAF + 3 models, summed across the watchlist)*

Each row click → loads the bottom panel for that airport.

**Bottom — Airport detail** (loaded on demand):

- 30-cell calendar heatmap from `airport_daily_summary`
- Diurnal strip from `airport_monthly_summary.diurnal_json`
- TAF-vs-models bar chart from `verification_monthly_stats` filtered by
  `icao`
- "Notable busts at this airport this month" — last few rows from
  `verification_scores` joined to `verification_observations`,
  click-through to the briefing pack
- One-line climatology summary string (plain text, generated server-side):
  *"30 days: 78% VFR, 12% IFR, 14 TS hours, foggiest at 06Z (28% IFR)."*

### URL state

`?tab=spotlight&month=2026-04&airport=LFPG&model=ecmwf` — same
encoder pattern as the existing `tab=accuracy`.

### Acceptance criteria

- Leaderboards card grid renders in <300ms for any past month.
- Featured airport card loads in <200ms on landing.
- Airport detail loads in <500ms.
- Calendar heatmap is interactive (hover for day detail).
- TAF-vs-models showdown names a winner per airport when sample size
  is sufficient (≥30 obs).
- Notable-bust links open the originating briefing pack.
- Existing `/admin/verification.html` continues to work unchanged
  (admin-only calibration dashboard preserved per decided choice).

### Risk notes

- Sample-size guard everywhere — leaderboards must filter
  `n_obs >= 50` (or similar) to avoid one-METAR airports topping every
  ranking.
- Privacy: notable-bust links to briefings need to either point at the
  airport-level map view (no flight context) or be admin-only. The
  raw bust data itself is anonymised at the verification-DB layer.

---

## How to brief a fresh agent for any phase

> "Read `designs/plans/patterns-and-spotlight.md`, find Phase N, and
> verify against the acceptance criteria there. The Status section at
> the top tells you what's already shipped. The decided-choices section
> tells you what's locked. If you have to deviate, update the plan."

Each phase brief above is self-contained: goal, files, approach,
expected impact, acceptance criteria, risk notes — same convention as
`designs/plans/refresh-pipeline-performance.md`.

## Open questions

All four planning questions have been resolved (see "Decided choices"
above). New questions surfaced during implementation should be added
here.

## References

- Source data design: [metar-taf-accuracy.md](../metar-taf-accuracy.md)
- Existing tab structure: [forecast-page.md](../forecast-page.md)
- Plan format precedent: [refresh-pipeline-performance.md](./refresh-pipeline-performance.md)
- Existing rollup pattern: `src/weatherbrief/tasks/verification_rollup.py`
- Existing cache pattern: `src/weatherbrief/tasks/cache_builder.py`
- URL state pattern: `web/ts/utils/url-state.ts` (PR #117)
