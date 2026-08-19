# Patterns & Spotlight — climatology rework of the Forecast tab

> **ARCHIVED 2026-08-17.** Phases 1–3 shipped, Phase 4 was abandoned (superseded
> by [`plans/verification-data-tiering.md`](../plans/verification-data-tiering.md)),
> Phase 5 shipped as the Climatology tab, Phase 6 was never built. The rollup-table
> shapes this doc introduced are now documented in
> [`designs/metar-taf-accuracy.md`](../metar-taf-accuracy.md).

> Replace the "Model Accuracy" map and "Accuracy Stats" dashboard with two
> climatology-first views (Patterns + Spotlight) backed by higher-cadence
> METAR ingest and pre-aggregated monthly/daily summaries. Reframes the
> question from "how good are the models?" (boring — they're all 80–85%)
> to "what is this airport actually like, and where do models trip?"

## Status (synced 2026-08-15)

**This plan is DONE as far as it is ever going to be. Nothing below is a
live work item.** Phases 1–3 shipped; Phase 4 was built, then deliberately
deleted; Phases 5–6 were superseded during implementation by a single
**Climatology** tab (issue #155) and the drill-down half was never built.
Read this doc only for the "why" — the current truth lives in
[metar-taf-accuracy.md](../metar-taf-accuracy.md) (ingest loops, rollups,
retention gating) and [forecast-page.md](../forecast-page.md) (the tab and
its sub-tabs). This doc is ready to archive.

- ✅ **Phase 1** — METAR ingest decoupling + 30-min cadence. Shipped (PR #140), deployed. `scheduler.py:run_metar_ingest_loop` → `tasks/standalone_verification.py:run_metar_ingest_cycle`, `DISABLE_METAR_INGEST=1` override.
- ✅ **Phase 2** — `airport_monthly_summary` rollup. Shipped. `db/models.py:AirportMonthlySummaryRow`, `tasks/airport_summary.py:rollup_month` / `rollup_all_complete_months`, migration `053_airport_summary_tables.py`, retention in `tasks/retention.py:prune_raw_observations`.
- ✅ **Phase 3** — `airport_daily_summary` rollup. Shipped. `AirportDailySummaryRow`, `rollup_day` / `rollup_all_complete_days` in `airport_summary.py`, migrations 053 + `057_airport_daily_n_category_changes.py`. Both rollups run from the same daily scheduler tick (`scheduler.py` ~line 750).
- ❌ **Phase 4** — MySQL partitioning. **Never landed and is not coming back.** The planned `054_partition_verification_observations.py` migration was never written (054 is `analytics_tables`); only a dormant `retention.py:ensure_future_partitions()` helper existed, and #522 (verification tiering, commit `1c748002`) deleted it as dead code. Retention is instead batched `DELETE` of whole months (`_delete_in_batches`, 5k rows/commit) double-gated on the climatology rollup **and** a verified Parquet archive manifest — see [verification-data-tiering.md](../plans/verification-data-tiering.md). Do not resurrect partitioning without re-reading that plan.
- 🟡 **Phases 5–6** — built as the **Climatology** tab, NOT as separate "Patterns" and "Spotlight" tabs. See "What actually shipped" below.

Don't reconstruct the rollup table shapes from anything here — the shipped
tables carry more percentiles, hours-below-threshold counters, and
different constraint names (`uq_ams_key`) than the plan sketched.
`db/models.py` is authoritative.

### What actually shipped (Phases 5–6 reframe)

- A single `data-tab="climatology"` tab in `web/maps.html` with **Map +
  "Top airports"** (leaderboard) sub-tabs (`#clim-subtabs`, not
  deep-linked). The old "Accuracy Stats" tab (`data-tab="stats"`,
  iframing `/verification.html?embed`) was **kept**, not replaced by a
  "Spotlight" tab. The tab row is now
  `forecast | synoptic | climatology | stats`.
- The **"Model Accuracy" map tab was deleted outright**, not renamed:
  `get_verification_map_data` / `rebuild_verification_map_cache` were
  dropped as not worth their ~30 min cache rebuild (see
  metar-taf-accuracy.md). So there is no per-airport model-accuracy map
  anywhere; the "Trust (per-model)" metric group in Phase 5 below never
  shipped in any form.
- Backend: `api/climatology.py` (`/climatology/category|phenomena|wind|volatility|leaderboard`)
  + `tasks/climatology_queries.py`. No `patterns.py` / `spotlight.py`.
  Read-only over the summary tables, **no cache layer** (indexed reads
  are cheap), and **auth-only** on every route (planned "Patterns =
  public" was dropped; gating note in the module docstring).
- Frontend: `web/ts/visualization/climatology-tab.ts`, `climatology-map.ts`,
  `climatology-datasets.ts`, `adapters/climatology-adapter.ts`,
  `web/ts/data/metrics-catalog.json` (also copied to the iOS app at
  `app/.../Resources/metrics-catalog.json`).
- **Not built**: the "Spotlight" per-airport drill-down — calendar
  heatmap, diurnal strip, featured-airport bust card, TAF-vs-models
  showdown, notable-bust → briefing-pack links. `calendar-heatmap.ts` /
  `diurnal-strip.ts` do not exist.

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
  (superseded by #522's archive-gated pruning)
- Add daily summary now (cheap, can't be backfilled later)
- ~~Partition `verification_observations` now while volume is small~~ — never done
- Tab names: **Patterns** + **Spotlight**
- Table names: `airport_monthly_summary` + `airport_daily_summary`
  (engineering-neutral; decoupled from the user-facing tab name)
- **Patterns default landing**: IR Usefulness, last 30 days
- **Spotlight default landing**: leaderboards + a "featured airport"
  card (auto-curated, e.g. biggest bust in the last 24h)
- ~~**Auth**: Patterns is **public** (climatology = SEO/discovery value);
  Spotlight is **auth-only**~~ — **reversed on implementation**: every
  `/climatology/*` route is auth-only (`Depends(current_user_id)`)
- **Admin dashboard fate**: existing `verification.html` is kept (served
  at `/verification.html`, admin-gated). Calibration-specific stats
  (cycle timings, error rates, raw MAE trends) live there; Spotlight
  is the user-facing surface.

## Phases

The phases are layered: 1–3 are pure infra and ship independently; 4–5
are user-facing and depend on rollup tables existing (but can render
against the live `verification_observations` table on day one if we want
to ship them before the rollup backfill completes).

- **Phase 1** — Decouple METAR ingest, run every 30 min ✅ shipped
- **Phase 2** — `airport_monthly_summary` rollup + retention task ✅ shipped
- **Phase 3** — `airport_daily_summary` rollup ✅ shipped
- **Phase 4** — ~~MySQL partitioning of `verification_observations`~~ ❌ abandoned — see Status block. Retention is gated month-at-a-time DELETE.
- **Phase 5** — Patterns tab (replaces Model Accuracy map) 🟡 shipped as the Climatology map, climatology metrics only
- **Phase 6** — Spotlight tab (replaces Accuracy Stats) ❌ only the leaderboard survived, as the "Top airports" sub-tab; Accuracy Stats was never replaced

Phases 1–3 are independent and can run in parallel. Phases 5–6 depend
on phase 2 (monthly summary) at minimum; phase 6's calendar heatmap
depends on phase 3 (daily summary).

---

## Phases 1–4 — the infra (shipped; detail removed)

The original per-phase briefs for the METAR-ingest split, the two rollup
tables and the abandoned partitioning ran ~350 lines here. They are gone
because every durable statement in them is now either code or documented
in a live design doc, and a stale second copy is worse than none:

- **Phase 1** (ingest split, 30-min cadence, 30-min dedup bucket, scoring
  becomes a pure-DB read, `DISABLE_METAR_INGEST`) → the loop table and
  cadences in [metar-taf-accuracy.md](../metar-taf-accuracy.md).
- **Phases 2–3** (monthly + daily rollup, idempotent DELETE+INSERT,
  `category_changes` computed in Python not SQL, diurnal JSON keyed by
  zero-padded HH) → `tasks/airport_summary.py` and the shipped ORM classes.
- **Phase 4** (partitioning) → abandoned; see the Status block.

Two decisions from those briefs are worth keeping because nothing else
records the reasoning:

- Retention was set at **180 days of raw obs** deliberately *while the
  rollup methodology was still being tuned* — the rollups were not yet
  trusted enough to be the only copy. #522 later replaced that with
  archive-gated month-at-a-time deletion, which is the stronger version of
  the same instinct.
- The daily table was added **at the same time as the monthly one even
  though nothing needed it yet**, because daily granularity cannot be
  reconstructed once the raw obs age out. That bet paid off: the current
  month's climatology is served by SUMming daily rows.

---

## Phase 5 — Patterns tab

### Goal

Replace the "Model Accuracy" tab with a Leaflet map whose default lens
is climatology, with model-trust metrics as alternate views.

> **Shipped, but not like this.** The file list, the `GET /maps/patterns`
> endpoint, `fetchPatternsMap()`, `get_patterns_map_data()` and the
> `patterns_map:*` cache keys that used to be spelled out here never
> existed. What shipped is `api/climatology.py` +
> `tasks/climatology_queries.py` + `climatology-tab.ts`, with **no cache
> layer at all** (indexed reads over ~830 rows are cheap enough) and
> auth on every route. Only the metric list and the risk notes below
> still describe reality.

### Metrics

**Patterns (observation-only, no model)** — these shipped, roughly:

- IR Usefulness — % hours IFR/LIFR
- VFR Reliability — % VFR hours
- Foggiest — % hours with BR/FG
- Stormiest — TS hour count
- Windiest — p95 wind / max gust
- Most Volatile — `category_changes` per obs

**Trust (per-model)** — optimistic/pessimistic busts, TS miss + false-alarm
rate, ceiling MAE. **None of these shipped**, and the per-airport accuracy
map they'd have extended was deleted. Anyone reviving them is starting
from scratch, not from a rename.

Period control shipped as a month picker over
`airport_monthly_summary`; the "Last 30 days" pseudo-month became
month-to-date, SUMmed from `airport_daily_summary` over completed UTC days
(`is_mtd` + `as_of_date` in the response).

### Risk notes (still live)

- Color scales need calibration — IR usefulness varies wildly (5% at
  EGLL, 35% at LFRB). Use a fixed 0–40% scale rather than auto-fit so
  cross-airport comparison is meaningful.
- "Most Volatile" is the noisiest metric; needs a minimum observation
  count threshold (n_obs >= 50) before ranking. Low-sample airports are
  rendered faded and omitted from the leaderboard rather than dropped.

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

## How to brief a fresh agent

Don't brief anyone off the phase sections — they describe intent that
partly did not survive contact with the code. If you're picking up the
never-built Spotlight drill-down (calendar heatmap, diurnal strip,
featured-bust card, TAF-vs-models showdown), read Phase 6 for the *idea*,
then read `climatology-tab.ts` + `climatology_queries.py` for the shape
the code actually settled on and design fresh against that.

## References

- Current truth, backend: [metar-taf-accuracy.md](../metar-taf-accuracy.md)
- Current truth, frontend tab: [forecast-page.md](../forecast-page.md)
- Retention/archival that replaced Phase 4: [verification-data-tiering.md](../plans/verification-data-tiering.md)
- Shipped code: `api/climatology.py`, `tasks/climatology_queries.py`,
  `tasks/airport_summary.py`, `web/ts/visualization/climatology-*.ts`
