# Production health reference

Interpretation data for the `check-health` skill (`.claude/skills/check-health/SKILL.md`).
That skill holds the *procedure* — what to run, in what order, and how to report. This doc
holds the *judgement*: expected bands, what a signal means before you flag it, and the
lessons that stop a normal condition being reported as a fault.

Read the section the skill points you at; you rarely need the whole file.

## Placeholders

`<user>@<server>`, `<data-volume>`, `<shared-infra-dir>`, `<admin-token>` and the compute-node
fields are all resolved per **`designs/references/deployment-paths.md`** — resolve them once at
the start of the check and reuse.

Two traps that doc covers and that matter here specifically: `<data-volume>` is the **mount
point**, not `HOST_DATA_DIR` (the disk bands below gauge the volume, so using the data dir
measures the wrong thing), and `DATA_DIR` in the server's `.env` is a *container* path.

## Sizing constants

These are deployment facts, not thresholds. A fork will have its own.

| Constant | Value |
|---|---|
| Droplet | 4 vCPU / 7.8 GB, 8 GiB swap |
| `weatherbrief` cgroup limit | 6 GiB |
| Data volume | 199 GB |
| App internal port | 8020 (HTTP, behind Caddy) |
| Containers we own | `weatherbrief`, `weatherbrief-mcp` (others on the box are siblings) |

## Thresholds at a glance

| Signal | ok | warn | issue |
|---|---|---|---|
| Memory peak_used % (24 h) | <80 % | >80 % | >90 % |
| Load 1m peak (4 vCPU) | <4 | >4 outside a cycle window | sustained >8 |
| `weatherbrief` container RSS | <4.5 GiB | >4.5 GiB | >5.5 GiB |
| Standalone cycle `peak_rss` | <3.5 GiB | >4.5 GiB | >5.5 GiB |
| Pipeline total | 30–90 s | >180 s | >300 s |
| p95 queue wait | <30 s | >30 s | — |
| Data volume used | 62–78 % | >85 % | >90 % |
| Filesystem `/` | <85 % | >85 % | — |
| GRIB pool resets | few/day | >5 in an hour | — |

Standalone cycle wall time, by flavour:

| Flavour | Expected | warn | issue |
|---|---|---|---|
| forecast (on the compute node) | 25–35 min | >45 min | >60 min |
| light (score-only) | 2–5 min | >10 min | — |
| imported (droplet ingest) | 60–90 s | stale >1 day | — |
| metar_ingest | seconds | — | — |

## Log-noise tiers

The error grep is the noisiest section. Bucket before reporting:

| Tier | Examples | What to do |
|---|---|---|
| **Real errors** | `pydantic_core...ValidationError`, `Failed to load route analyses`, `Auto-refresh failed`, any `Traceback` not wrapping an upstream timeout | Flag with the file/manifest path — this is what silently breaks a feature. |
| **Self-alerts** (app monitoring itself) | `Standalone cycle memory anomaly`, `Memory high-water mark crossed`, `Memory anomaly check failed`, `MemorySampler tick failed` | Highest-signal warnings — surface prominently. |
| **Recurring upstream noise** | `AvWx fetch failed`, `DWD chart fetch failed`, `Open-Meteo 502`, `Dropping DD cloud deck ... degenerate layer geometry`, `compute_route_distance: dropped ... waypoint(s)` | Count and report a total; do not enumerate. Flag only on a rate jump. |

Scan traffic (`GET /joomla/.env`, `error_log.php`, `wp-login.php`, `.git/`, `xmlrpc`) is
filtered out by the skill's second grep. See §L6.

## Upstream steady-state rates

| Upstream | Steady-state | Flag when | Impact |
|---|---|---|---|
| **Open-Meteo** (`Failed to fetch metadata for ...: Server error '502'`) | 0–3 per hour | >5/h, or sustained failures across multiple models at once | Briefing falls back to remaining models. Multi-model concurrent failure → check egress. |
| **AvWx / aviationweather.gov** (`Read timed out`) | 10–25 per day | >5/h sustained | US METAR/TAF unavailable for that briefing — non-fatal for European routes. |
| **DWD charts** (`DWD chart fetch failed`) | 0–5 per day | >10/day | Frontal charts missing — non-fatal, briefing still ships. |

`Failed to fetch <model>` *without* "metadata for" is the actual data pull failing, not the
catalogue probe. Always flag it.

---

## Known lessons (apply before flagging)

### §L1 — Low CPU + slow stage = GIL / IPC, not cores

If `Pipeline timing:` is slow but CPU is well below `100% × n_cpus` and load is below
`n_cpus`, do **not** suggest adding workers or cores. The decode pool is already a
`ProcessPoolExecutor`; the next levers are vectorisation or scheduling (don't overlap the
forecast cycle with peak user time). Reference: a droplet capture at 32 % CPU, load 1.37,
showing 3–6× slower decode.

### §L2 — cgroup ≠ memory pressure for the standalone cycle (RSS is what matters)

**Headline:** `peak_cgroup` will almost always pin near the cgroup limit during a standalone
forecast cycle. That is **not** an OOM risk signal on its own — it's the kernel doing its job.

Why: the cycle does heavy compute on GRIB files via cfgrib's mmap path. The page cache fills
opportunistically with those file-backed pages, up to the cgroup limit. Those pages are
*reclaimable* (file-backed, clean) — when the next allocation needs anonymous memory, the
kernel evicts them with no swap or OOM impact. So `peak_cgroup` is roughly
`peak_rss + (whatever file cache fits before the limit)`: a function of the *limit*, not of
the workload's demand.

The real demand signal is **`peak_rss`** (anonymous + actually-resident pages), logged
alongside it in `Standalone cycle peaks: rss=<N>MB cgroup=<N>MB`.

How to apply:

- Assess risk against `peak_rss`, not `peak_cgroup`.
- Healthy band for `peak_rss` during a forecast cycle: up to ~3.5 GiB. >4.5 GiB is a real warn.
- The app's `Standalone cycle memory anomaly` alert currently thresholds on `peak_cgroup`, so
  it fires routinely on cycles doing real GRIB work. **Don't treat it firing as automatically
  a problem** — drill into `peak_rss` to decide. (Re-thresholding the alert on RSS is a
  separate cleanup; until then this doc is the interpretation layer.)
- Where it *does* matter: if `peak_rss` is also high (>4.5 GiB) and a user refresh lands
  mid-cycle, the cgroup has no room left even after page-cache reclaim — that's the genuine
  OOM path.
- Per-pid container swap (`VmSwap` in `Memory after ...` lines) is a better "is the cgroup
  actually under pressure" signal than container mem usage; host-level swap can include mmap
  pages from other workloads and is less reliable.

### §L3 — "Pool stuck" is the recovery path

`GRIB decode pool stuck (worker hung 300s on <fn>); resetting` is the **fix** firing, not the
bug. The pool shuts down with `wait=False` and the next call rebuilds it. A handful per day is
acceptable. A burst is the signal — correlate with droplet metrics and disk I/O.

### §L4 — RSS high-water marks drift

Peak RSS climbing ~1.8 → ~2.6 GB over 24 requests on a fixed cgroup is the **expected**
high-water-mark random walk on Python heap fragmentation, not a leak. A leak signature is a
monotonic climb of *baseline* (`start` checkpoint) values across many requests with no GC
reclaims. Don't propose nightly restarts. Revisit only if peak crosses ~5.5 GiB on the 6 GiB
cgroup, or deploy frequency drops below once a week.

### §L5 — Don't suggest a droplet downgrade

The 4 vCPU / 7.8 GB sizing was chosen on a miscalculation, but downgrading requires a full
rebuild and won't happen. Frame spare capacity as headroom for new work, not waste. The real
cost levers are LLM digest spend and the ECMWF order tier.

### §L6 — Scan traffic is noise

`GET /joomla/.env`, `error_log.php`, `wp-login.php`, `.git/`, `xmlrpc` and friends are random
attack scans hitting the front door. Filter them out of any error count; mention only if the
rate is unusually high.

### §L7 — Cycle hours overlap with user peak

06:15 / 09:15 / 12:15 / 15:15 / 18:15 UTC are the **light** cycle hours. Load spikes and
slower briefings *during* those windows are expected — a briefing taking 2× normal at
09:20 UTC is not the same problem as one taking 2× at 02:00 UTC.

The heavy forecast cycle (07:15 / 19:15 UTC) runs **off-box on a compute node**, so those two
hours are no longer a load excuse on the droplet — it only *imports* the artifact (~72 s).
Load above the vCPU line outside a light-cycle hour is most often concurrent user briefings;
check pipeline timings before assuming a cycle.

### §L8 — `docker compose` v2, not `docker-compose`

On this droplet the binary is `docker compose` (two words).

### §L9 — Disk composition (steady-state vs growing)

The data volume splits into **rotating** (bounded by TTL) and **growing** (bounded only by
retention, or nothing):

| Component | Path (under `<data-volume>`) | Type | Expected band | Notes |
|---|---|---|---|---|
| **ICON-D2 GRIB cache** | `weather/data/.cache/grib/icon-d2/` | rotating | **30–45 GB** | 6 h TTL, 8 runs/day. Hard cap **45 GiB** (`_DEFAULT_CACHE_CAP_GIB`), oldest-init-first eviction with a 2-run floor. Override via `WB_GRIB_CACHE_CAP_GB_ICON_D2`. Cap is binary GiB, `du` reports decimal GB. Largest single component — check it first when disk moves. |
| ICON-EU GRIB cache | `weather/data/.cache/grib/icon-eu/` | rotating | **30–50 GB** | 12 h TTL × ~4 runs/window × ~10 GB/run. Uncapped. Logs `Purged N old icon-eu GRIB cache dirs` only when N>0. |
| GFS GRIB cache | `weather/data/.cache/grib/gfs/` | rotating | **1–5 GB** | 24 h TTL × ~5 runs. Uncapped (cheap). |
| ECMWF deliveries | `ecmwf/data` | rotating | **13–25 GB** | 36 h TTL via `purge_old_ecmwf_deliveries`. |
| SRTM terrain | `weather/data/.cache/srtm/` | constant | **~4.4 GB** | Never aged. |
| Pack store | `weather/data/packs/` | growing | see headroom math below | Monotonic until T1 strip (30 d) and T2 delete (90/180 d). |
| MySQL data | `mysql/<db>/*.ibd` | growing | **4–6 GB** (app DB) | Dominated by `verification_scores` (~2.7 GB); grows with verification output. |
| MySQL binlogs | `mysql/binlog.0000XX` | rotating | **~10 GB** (~10 × 1.1 GB, ~30 d span) | Auto-purged at `binlog_expire_logs_seconds`. |
| Sandboxes / forms / logs | misc | constant | **<1 GB combined** | Ignore unless growing. |

**Flag the individual component that's out of band, not the total.** A total of 71 % isn't a
warn if every component is in its band — it's just healthy rotating caches plus the slowly
growing pack store. The total only becomes a warn when a component breaks out (binlogs
spanning >40 d, or `packs/` over the headroom budget).

**The two GRIB caches are the swing factor.** ICON-D2 (≤45 GiB capped) + ICON-EU (uncapped,
up to ~50 GB) jointly range from ~60 GB to ~98 GB. That ~38 GB swing is larger than the entire
pack store, so a total-usage jump is far more likely to be cache phase than real growth.
Always break the total down before concluding anything.

**Headroom math** — subtract fixed components from the volume to get the pack/mysql budget:

- Caches (ICON-D2 33 + ICON-EU 30 + GFS 4.8 + ECMWF 13 + SRTM 4.4): **~85 GB** steady-state
- MySQL (data + binlogs): ~12 GB
- Other (sandboxes, forms, logs): ~1 GB
- → **Pack budget before the 85 % warn: ~71 GB**; before the 90 % issue: ~81 GB

**Worst-case caveat:** that assumes caches at their *observed* 85 GB. Their *ceiling* is much
higher — ICON-D2 capped at ~48 GB and ICON-EU reaching ~50 GB would put caches near 130 GB and
shrink the pack budget to ~25 GB. If both sit high at once, disk gets tight far faster than the
steady-state number suggests. Check the per-cache lines before trusting the headroom figure.

### §L10 — MySQL RSS is not proof of configuration

`docker stats` shows resident memory only, so an InnoDB buffer pool that got **swapped out**
reads low while the config is still correct. Confirm with `SHOW VARIABLES LIKE
'innodb_buffer_pool_size'` before concluding the setting was lost. Config right + RSS low +
swap high = paged-out pool: not a regression, but DB reads are hitting disk, and a
`docker compose restart shared-mysql` in a quiet window faults it back in.

On a long-uptime box, cold swap pages accumulate without indicating live pressure — read
`free -h` **available** and current load before flagging swap at all.

### §L11 — Retention silence is not retention failure

- `Retention applied: T1=N packs, T2=M packs, freed=X.X MB` covers **briefing packs only**,
  not GRIB caches. `freed=0.0 MB` is fine when no pack crossed a TTL boundary in the window.
- `Purged N old <model> GRIB cache dirs` is logged **only when N > 0**. A quiet purge is a
  successful no-op, indistinguishable from "didn't run". To prove the loop is alive, look for
  `Retention applied:` (which always logs) — the GRIB purge runs in the same pass.
- `Retention cycle failed` / `GRIB cache purge failed` / `ECMWF delivery purge failed` are all
  real issues; the pass aborted mid-way.
- If `Retention applied:` is missing for >36 h, the loop is wedged.

---

## Standalone cycle detail

The heavy forecast work runs on a **compute node**, which emits a SQLite artifact into
`<data-volume>/weather/snapshot_inbox/` that the droplet imports. Ground truth is the DB, not
the logs — the import is quiet in `docker logs`.

The local `standalone_forecast` path still exists as a fallback but is a **cold path**: the
droplet sets `STANDALONE_ANALYSIS_WORKERS=0`, so if it ever does run it falls back to inline
single-core (~68 min) rather than the 25–35 min band above. A local `standalone_forecast` line
appearing on the droplet means the fallback fired — investigate why the artifact didn't arrive.

The inbox is **append-only in practice** — artifacts are not deleted after import. Harmless at
the current rate, but it is not self-pruning.

Three cycle flavours, each with its own `Standalone <type> cycle:` summary line — don't
conflate them:

- **light** — scoring + rollup + stats/leaderboard cache rebuild. No model fetch, no
  forecast-map rebuild. Runs at the five light hours.
- **forecast** — heavy model fetch (gfs / icon / ecmwf; no UKMO in standalone), then
  forecast-map cache rebuild only. Rollup and stats/leaderboard are skipped because the cycle
  creates no scores. Runs on the compute node.
- **metar_ingest** — observation pull, every 30 min, cheap.

What to read:

- **Cache-rebuild breakdown** — `Cache rebuild: N stats (Xms) + M bias_leaderboard (Yms) +
  K forecast_map (Zms) entries (Tms total)`. Stats should be **seconds** (tens at worst).
  Minutes again means the `COUNT(DISTINCT)` plan regressed — check the `get_digest_data(...)`
  INFO breakdown line (logged only when >5 s) for the culprit sub-query, and confirm index
  `ix_verif_scores_source_time` still exists.
- After a **forecast** cycle, `Standalone forecast cycle: skipping rollup + stats/leaderboard
  cache` must appear and `cache rebuilt (Nms)` should be **<60 s** (forecast-map only). A
  forecast cycle spending minutes there means the cycle-aware skip regressed.
- **Hard failure**: any SQL error naming `ix_verif_scores_source_time` (e.g. MySQL 1176 "Key
  ... doesn't exist"). The activity queries `FORCE INDEX` it by name; if it's dropped, every
  dashboard/digest stats call hard-fails. Always an issue.
- **Sounding-analysis timings** — `Model <m> chunk N/7: analyzed S snapshots in Xs` and
  `ECMWF GRIB leg: N steps, S snapshots, sounding analysis Xs`. Every one must carry a
  **`(pooled)`** suffix, and the child must log `GRIB decode pool started (workers=2` (the
  cycle child uses 2; the main app pool uses 3 — don't confuse them). Worker-side proof of
  parallelism: `analyze_sounding_batch: .* profiles dur=` with two distinct `pid=` values
  interleaving. **If `(pooled)` is missing**, pooling didn't engage and the cycle fell back to
  inline single-core — check the child got `GRIB_DECODE_WORKERS=2` (overridden by
  `STANDALONE_ANALYSIS_WORKERS`; `0` = deliberate rollback to inline).
- **Equivalence check** — snapshots stored ~54–56K (`Model .*: stored .* snapshots`), and
  sounding analysis actually ran: `SELECT COUNT(*) ... WHERE model_init_time > <today> AND
  sounding_cape_jkg IS NOT NULL` should be **~99 %** (CAPE is computed for every analysed
  profile; ~1 % gap is normal out-of-bounds profiles). Do **not** use `sounding_ceiling_ft` —
  it is weather-conditional (NULL = clear) and normally ~53 %. A big CAPE drop means batches
  are failing silently; grep `Pooled sounding analysis` warnings (should be absent/rare).
- **Pool teardown** — on a healthy cycle, `GRIB decode pool shut down (wait=True, ...
  workers=[])` appears shortly before the cache rebuild, and the child exits promptly after
  `cache rebuilt` (no gap = no atexit hang). A `force-terminated` line is the failure-path net
  (TERM→KILL of a wedged worker): expected absent on a clean run, investigate if present.
- `Recorded failed forecast cycle` / `Recorded failed verification cycle` — the cycle threw;
  the row in `verification_cycles` carries the traceback. Always investigate.

If standalone log lines are entirely missing from a window when one was due, the scheduler may
be wedged (rare). Check for `Auto-refresh scheduler started` / `Retention loop started` near
the container start time.

---

## Baseline history

Kept short and dated deliberately — these are the observations the bands above were set from.
Add a line when a band is re-baselined; don't narrate inside the bands themselves.

- **Volume resize** — data volume grew 149 GB → 199 GB; the ICON-D2 cache arrived at the same
  time. Bands in §L9 are post-resize.
- **MySQL buffer pool** — `innodb_buffer_pool_size` raised 128 MB → 1 GiB, so `shared-mysql`
  sits at ~1.2–1.6 GiB instead of the old ~330 MiB. A reading back near ~330 MiB means the
  container restarted *without* the compose-file setting: re-run `docker compose up -d` in
  `<shared-infra-dir>`, or re-apply `SET GLOBAL innodb_buffer_pool_size`. See §L10 before
  concluding that from RSS alone.
- **cgroup limit** — raised 3 → 4 → 6 GiB. Older notes citing 3 or 4 GiB are stale.
- **Heavy cycle moved off-box** — the droplet stopped running `standalone_forecast` locally
  and now imports the compute node's artifact (~72 s, twice daily). §L7 was rewritten for this.
- **Disk band floor widened** 68 % → 62 % after observing a 10 GB single-day drop that was
  purely ICON cache phase. A drop is as unremarkable as a rise.
- **Pack efficiency** — per-flight average came down from ~100 MB to ~73 MB as T1 stripping
  started recovering bytes.
