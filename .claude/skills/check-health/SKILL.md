---
name: check-health
description: End-to-end production health check for weatherbrief on the flyfun.aero droplet — droplet metrics, container state, log signals, refresh queue, standalone cycle, RSS growth
disable-model-invocation: true
---

# Production health check

Use when the user asks "is prod ok?", "check droplet health", "anything broken?", or before/after a deploy as a smoke check. Pull data, interpret it through the known-lessons lens below, then report a short verdict — not a wall of raw output.

**Target:** `weather.flyfun.aero` droplet (DO id 535197551, 4 vCPU / 7.8 GB, region `lon1`). SSH as `brice@161.35.35.15`.

**Default window:** last 1 hour for live triage, last 24 h for a daily look. Take an argument if the user gives one (e.g. "since 07:00 UTC").

---

## How to run

Step through the sections below in order. Run independent checks in parallel where you can — most of these are read-only and fast.

At the end produce **one** summary block:

```
verdict: <ok | warn | issue>
1-line headline
- bullet for each section with a status (ok / note / warn / issue) and the key number
followed by: a tight 2-4 bullet "what to look at" if verdict != ok
```

Don't dump raw logs or full metric tables. The user wants the synthesis.

---

## 1. Droplet metrics (DO API)

```bash
python3 ~/.claude/skills/droplet-metrics/metrics.py --minutes <window>
```

Look at:

- **Memory peak_used %** — >80 % over 24 h is a warn; >90 % is an issue. Swap is also relevant (next section).
- **Load 1m peak vs vcpus (=4)** — peaks above 4 mean the run queue saturated. A peak in the 5–8 range during a standalone forecast cycle (06:15 / 09:15 / etc. UTC) is normal; outside those windows it's a warn.
- **CPU avg vs peak** — *low CPU + high latency* is the classic GIL/IPC signature. See lesson §L1.
- **FS used_peak** — `/` >85 % or `/mnt/flyfun_data` >85 % is an issue (briefings will start failing).
- **Net peak** — informational; sustained near-link-rate during a fetch window is fine, outside is noise.

Disk I/O is **not** exposed by the DO API. If CPU+load look fine but something is slow, ssh in and run `iostat -xm 5 3` to rule out disk before assuming GIL.

## 2. Container state + host pressure

```bash
ssh brice@161.35.35.15 "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' && echo --- \
  && docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' \
  && echo --- && free -h && echo --- && uptime \
  && echo --- && df -h / /mnt/flyfun_data"
```

Read:

- All containers `(healthy)`. `weatherbrief` and `weatherbrief-mcp` are the two we own; the rest are siblings.
- `weatherbrief` MEM USAGE / LIMIT — cgroup limit is **6 GiB** (raised from 4 GiB; do not cite the older "3 GiB" / "4 GiB" numbers from memory). Sustained >4.5 GiB warrants a note.
- `shared-mysql` at **~1.2–1.6 GiB is expected and healthy** since 2026-07-18: `innodb_buffer_pool_size` was raised 128 MB → 1 GiB (flyfun-weather#448). Don't flag the jump from the old ~330 MiB baseline. Do flag if it reads ~330 MiB again — that means the container restarted *without* the compose-file setting (run `docker compose up -d` in `~/digitalocean/shared-infra`, or re-apply `SET GLOBAL innodb_buffer_pool_size`).
  - **A low RSS here does not prove the setting was lost.** `docker stats` shows resident memory only, so a buffer pool that got **swapped out** reads low while the config is still correct. Observed 2026-07-28: RSS 542 MiB — below the expected band — but `innodb_buffer_pool_size` was still `1073741824` and `mysqld` held 2.3 GiB in swap. Confirm before concluding:
    ```bash
    ssh brice@161.35.35.15 "docker exec shared-mysql mysql -uroot -p\$(grep -h MYSQL_ROOT_PASSWORD ~/digitalocean/shared-infra/.env | cut -d= -f2) -e \"SHOW VARIABLES LIKE 'innodb_buffer_pool_size'\""
    ```
    Config right + RSS low + swap high = paged-out pool. Not a config regression, but DB reads are hitting disk; a `docker compose restart shared-mysql` in a quiet window faults it back in.
- Host **Swap used** — the droplet has **8 GiB** swap (not the 4 GiB this doc claimed until 2026-07-28). Anything more than a few hundred MB is nominally a warn, but read *who holds it* before flagging: production sometimes parks 1–2 GiB of mmap-backed cfgrib pages there under memory pressure (see lesson §L2). Attribute it before judging:
  ```bash
  ssh brice@161.35.35.15 "for f in /proc/*/status; do awk '/^Name:/{n=\$2}/^VmSwap:/{if(\$2>50000) print \$2\" kB  \"n}' \$f 2>/dev/null; done | sort -rn | head -12"
  ```
  On a long-uptime box (87 days at the 2026-07-28 observation) cold pages accumulate without indicating live pressure — check `free -h` **available** and current load before calling it. Observed 2026-07-28: 3.9 GiB total swap, **2.3 GiB of it `mysqld`**, with available at 3.9 GiB and load 0.53 → note, not issue.
- Host **free Mem** — single-digit MB free is fine if buff/cache is large; what matters is `available`.
- `uptime` load triplet — same interpretation as the DO metrics, just current.

## 3. Recent errors & warnings

Pull the last window from the container. The `weatherbrief` log buffer is the primary source; `journalctl CONTAINER_NAME=weatherbrief` survives rebuilds if you need history past a deploy.

```bash
ssh brice@161.35.35.15 \
  "docker logs --since <window> weatherbrief 2>&1 \
   | grep -iE 'error|exception|traceback|killed|oom|fatal|critical|broken|stuck|warning' \
   | grep -vE '\.env HTTP|error_log\.php|joomla|wp-admin|wp-login|/\.git/|xmlrpc|phpinfo|setup\.php' \
   | head -80"
```

The second grep strips known scan noise (random WordPress / Joomla probe URLs). Don't include those in the verdict.

**Bucket the remaining warnings into three tiers**, since this is the noisiest section by far:

| Tier | Examples | What to do |
|---|---|---|
| **Real errors** | `pydantic_core...ValidationError`, `Failed to load route analyses`, `Auto-refresh failed`, anything with `Traceback` not wrapping an upstream timeout | Flag in summary with file/manifest path; this is the kind of thing that silently breaks a feature. |
| **Self-alerts** (app monitoring its own health) | `Standalone cycle memory anomaly`, `Memory high-water mark crossed`, `Memory anomaly check failed`, `MemorySampler tick failed` | These are the app *telling you* about its state — surface them prominently, they're the highest-signal warnings. |
| **Recurring upstream noise** | `AvWx fetch failed for metar/taf: ... aviationweather.gov ... Read timed out` (aviationweather.gov flaps constantly), `DWD chart fetch failed`, `Open-Meteo 502`, `Dropping DD cloud deck ... degenerate layer geometry`, `compute_route_distance: dropped ... waypoint(s)` | Count and report a total, do not enumerate. Only flag if rate jumps (e.g. >30 AvWx failures/h is unusual, ~15-20/h is steady-state). |

Quick bucketing one-liner once you have the filtered log:
```bash
... | awk -F: '{print $1"  "$2}' | sort | uniq -c | sort -rn | head -10
```

Look specifically for the patterns in §4–§7. Anything else is bucketed as "other" and only mentioned if non-trivial.

## 4. Refresh queue & briefing pipeline

Two complementary signals: **live queue state** (cheap admin endpoint) and **historical pipeline timing** (grep logs).

### 4a. Live queue (admin endpoint)

The app exposes `/api/admin/metrics` (auth required). From the droplet, hit it via the internal port:

```bash
ssh brice@161.35.35.15 \
  "curl -s -H 'Cookie: flyfun_auth=<admin-token>' http://127.0.0.1:8020/api/admin/metrics | python3 -m json.tool | head -40"
```

If the user hasn't supplied a token, skip this check and rely on log greps. Don't fish for the token from disk.

`active_refreshes` and `queued_refreshes`: 0–2 active is normal; persistently >0 `queued` means the cap is biting. `p95_elapsed` and `p95_queue_wait` over the last hour/day tell you whether briefings are fast — flag p95_elapsed >180 s or p95_queue_wait >30 s.

### 4b. Pipeline timing greps

```bash
ssh brice@161.35.35.15 "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'Pipeline timing:|Auto-refresh (completed|failed|skipping)|Background refresh complete|QueueFullError|Briefing refresh queued'"
```

Read:

- `Pipeline timing: ... total=<N>s` — typical total is 30–90 s. >180 s is a warn, >300 s an issue. Diagnose by the dominant stage:
  - `fetch=` dominant → Open-Meteo upstream (or AvWx timing out — see §3 noise tier)
  - `analyze=` dominant → GIL/decode contention (rare since ProcessPool, see §L1/§L3)
  - `llm_digest=` dominant → LLM call (cost driver, not perf bug). 35–45 s is the steady-state band; consistently >60 s suggests model latency or oversized context.
  - `dwd_charts=` dominant (~30 s) → dwd.de timing out (see §8); briefing still completes, just slower.
- `Auto-refresh: skipping <id> (refresh already in progress)` — single occurrence is fine; bursts indicate a stuck worker.
- `Auto-refresh failed for <id>` — always investigate (full traceback follows).
- `QueueFullError` / `Refresh queue full (N active)` — the queue depth cap fired; flag with the count.

## 5. GRIB ProcessPool

```bash
ssh brice@161.35.35.15 "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'GRIB decode pool stuck|BrokenProcessPool|GRIB2 enrichment:|No .* GRIB2 data retrieved'"
```

- `GRIB decode pool stuck (...); resetting` — see lesson §L3. A few per day is acceptable; >5 in an hour, or a burst correlated to a specific model, is a real warn.
- `BrokenProcessPool` mention in a Python traceback is a worker SIGKILL/OOM. Cross-reference RSS / cgroup at the same timestamp.
- `No ICON-EU GRIB2 data retrieved for enrichment` / `No GRIB2 CLWMR/ICMR data retrieved` — model fetch failed upstream; usually transient. Flag only on repeated failures across cycles.

## 6. Standalone verification cycle

Schedule: **light** (score-only) cycles at 06:15 / 09:15 / 12:15 / 15:15 / 18:15 UTC; **METAR ingest** every 30 min; and at **07:15 / 19:15 UTC** the heavy forecast work — which **no longer runs on the droplet** (see below).

> **⚠️ The droplet does not run the heavy forecast cycle any more.** It is produced off-box by the `mac-mini-m4` compute node (`deploy/compute-nodes.json`), which emits a SQLite artifact into `/mnt/flyfun_data/weather/snapshot_inbox/` and the droplet **imports** it. Verified 2026-07-28: the last local `standalone_forecast` cycle was **2026-07-26 07:15** (~45 min), while `standalone_imported` runs twice daily and takes **~72 s**. The node's runner script still carries a stale "VALIDATION PHASE: prod does NOT ingest yet" comment — ignore it, ingest is live.
>
> Ground truth is the DB, not the logs (the import is quiet in `docker logs`):
> ```bash
> ssh brice@161.35.35.15 "docker exec weatherbrief python -c \"
> from weatherbrief.db import get_engine
> from sqlalchemy import text
> with get_engine().connect() as c:
>     for r in c.execute(text('''SELECT source, COUNT(*), MAX(started_at), AVG(duration_ms)/1000
>         FROM verification_cycles WHERE started_at > UTC_TIMESTAMP() - INTERVAL 3 DAY
>         GROUP BY source ORDER BY 2 DESC''')): print(r)
> \""
> ```
> Expect `standalone_imported` ~2/day at ~60–90 s. **If `standalone_imported` goes stale, the node stopped delivering** — check it is reachable and that its cycle ran (`~/flyfun-data/logs/`). The local `standalone_forecast` path still exists as a fallback but is a **cold path**: it has not executed since 2026-07-26, and the droplet sets `STANDALONE_ANALYSIS_WORKERS=0`, so if it ever does run it falls back to inline single-core (~68 min, the pre-PR-B figure) rather than the ~25–35 min band below. Treat the bands below as applying to the **node's** cycle.
>
> Note the inbox is **append-only in practice** — artifacts are not deleted after import (10 files × ~14 MB on 2026-07-28). Harmless at that rate, but it is not self-pruning.

Grep the latest cycle's footprint:

```bash
ssh brice@161.35.35.15 "docker logs --since 24h weatherbrief 2>&1 \
  | grep -E 'Standalone .* cycle:|Standalone cycle peaks:|Standalone cycle RSS @|Standalone cycle memory anomaly|Recorded failed .* cycle|Memory anomaly check failed|ECMWF a. decode failed|Cache rebuild:|ECMWF GRIB leg:|get_digest_data|analyzed [0-9]+ snapshots|\(pooled\)|GRIB decode pool (started|shut down)|force-terminated'"
```

The cycle has **three flavours**, each with its own `Standalone <type> cycle:` summary line. Don't conflate them:
- **light** — scoring + rollup + stats/leaderboard cache rebuild (no model fetch, no forecast-map rebuild). Runs at the five light hours.
- **forecast** — heavy model fetch (gfs / icon / ecmwf — no UKMO in standalone), then forecast-map cache rebuild ONLY (rollup + stats/leaderboard are skipped since #448: the cycle creates no scores). Runs at 07:15 / 19:15 **on the compute node**, not the droplet — the droplet sees the result as a `standalone_imported` cycle (~72 s). A local `standalone_forecast` line appearing on the droplet means the fallback fired: worth investigating why the artifact didn't arrive.
- **metar_ingest** — observation pull, every 30 min, cheap.

Read:

- `Standalone <type> cycle: M models, S snapshots, ... (Tms)` — post-#448 + post-PR-B (#450) expected bands. PR B moved the ~68-min single-core sounding analysis to a 2-worker process pool inside the cycle child, roughly halving the fetch phase:
  - **forecast**: **~25–35 min** total wall. **>45 min is a warn, >60 min an issue** (>60 min usually means the pool did not engage — see the `(pooled)` check below). Band set from the PR-B estimate; confirm/retune against the first few validated droplet cycles. (Pre-PR-B was ~65–80 min.)
  - **light**: ~2–5 min total wall. >10 min is a warn — check the `Cache rebuild:` breakdown below.
- **Cache-rebuild breakdown (#448 instrumentation)** — `Cache rebuild: N stats (Xms) + M bias_leaderboard (Yms) + K forecast_map (Zms) entries (Tms total)`:
  - stats should be **seconds** (tens of seconds worst case). Minutes again = the `COUNT(DISTINCT)` plan regressed — check the `get_digest_data(...)` INFO breakdown line (only logged when >5 s) for which sub-query, and confirm index `ix_verif_scores_source_time` still exists.
  - After a **forecast** cycle the line `Standalone forecast cycle: skipping rollup + stats/leaderboard cache` must appear and `cache rebuilt (Nms)` should be **<60 s** (forecast-map only). A forecast cycle spending minutes in cache rebuild = the cycle-aware skip regressed.
- **Hard failure to grep for**: any SQL error mentioning `ix_verif_scores_source_time` (e.g. MySQL 1176 "Key ... doesn't exist") — the activity queries FORCE INDEX it by name; if that index is ever dropped, every dashboard/digest stats call hard-fails. Always an issue.
- **Sounding-analysis timings** — `Model <m> chunk N/7: analyzed S snapshots in Xs` (gfs/icon, ~2 chunks in flight) and `ECMWF GRIB leg: N steps, S snapshots, sounding analysis Xs`. Post-PR-B every one of these lines must carry a **`(pooled)`** suffix, and the child must log `GRIB decode pool started (workers=2` (workers=2 is the cycle child; the main app pool is workers=3 — don't confuse them). Worker-side proof of parallelism: grep `analyze_sounding_batch: .* profiles dur=` — two distinct `pid=` values interleaving = both workers busy. **If `(pooled)` is missing**, pooling did not engage (cycle falls back to inline ~68-min single-core) — check the child got `GRIB_DECODE_WORKERS=2` (overridden by `STANDALONE_ANALYSIS_WORKERS`; `0` = deliberate rollback to inline).
- **Equivalence check** — snapshots stored ~54–56K (`Model .*: stored .* snapshots`), and sounding analysis actually ran: `SELECT COUNT(*) ... WHERE model_init_time > <today> AND sounding_cape_jkg IS NOT NULL` should be **~99%** (CAPE is computed for every analysed profile; ~1% gap = normal out-of-bounds profiles). Do **not** use `sounding_ceiling_ft` — it is weather-conditional (NULL = clear) and normally ~53%. A big `cape` drop = batches failing silently; grep `Pooled sounding analysis` warnings (should be absent/rare).
- **Pool teardown (PR B)** — on a healthy cycle grep `GRIB decode pool shut down (wait=True, ... workers=[])` shortly before the cache rebuild, and the child must exit promptly after `cache rebuilt` (no gap = no atexit hang). A `force-terminated` line is the failure-path net (TERM→KILL of a wedged worker) — expected absent on a clean run; its presence = a worker had to be killed, investigate.
- `Standalone cycle peaks: rss=<N>MB cgroup=<N>MB samples=<N>` — **read `rss=`, not `cgroup=`** (see §L2). RSS is actual demand; cgroup will pin near the limit just from mmap'd GRIB cache and is not a pressure signal on its own. Flag when `rss=` >4.5 GiB.
- **`Standalone cycle memory anomaly (source=...): peak_rss=<N>MB peak_cgroup=<N> baseline=<N> cgroup_limit=6144 (relative_threshold=<R>, absolute_threshold=<A>)`** — the app's own self-alert, currently thresholded on `peak_cgroup`. Because of §L2 this fires routinely without indicating a real problem; **read `peak_rss` to decide**:
  - `peak_rss` well under 4 GiB → false positive; mention in the summary but rank as note, not warn.
  - `peak_rss` >4.5 GiB → real warn; cross-check §4b for an overlapping user briefing (would have made cgroup actually tight).
  - `peak_rss` >5.5 GiB → issue; concurrent refresh would have OOM-killed.
- `Recorded failed forecast cycle` / `Recorded failed verification cycle` — the cycle threw; the row in `verification_cycles` will have the traceback. Always investigate.

If the standalone cycle log lines are entirely missing from a window when one was due, the scheduler may be wedged (rare). Check `Auto-refresh scheduler started` / `Retention loop started` near the container start time to confirm the scheduler initialised.

## 7. RSS growth & memory hygiene

```bash
ssh brice@161.35.35.15 "docker logs --since 24h weatherbrief 2>&1 \
  | grep -E 'Memory high-water mark crossed|Memory curve:|Memory after .*: rss=|MemorySampler tick failed'"
```

Read:

- `Memory high-water mark crossed +<N> MB step: <prev> → <curr> MB after <stage>` — this fires every 500 MiB step crossed. Two or three in 24 h is fine; a steady climb step-by-step with no plateau is a leak signature. Cross-check against the 2026-04-28 baseline: peak RSS drifted 1846 → 2619 MB over ~24 requests in 24 h *without* a leak — high-water-mark random walk is expected (see lesson §L4).
- `Memory curve: ... request_growth=<+N> MB` — per-request growth. Net-positive sums over many requests can mean leak; isolated large positives are normal (mid-pipeline allocations).
- `MemorySampler tick failed` — the background sampler errored; not user-impacting but worth a note if it repeats.

## 8. Model & chart upstream health

```bash
ssh brice@161.35.35.15 "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'Failed to fetch metadata for|Failed to fetch .*: |returned no values|AvWx fetch failed|DWD chart fetch failed'"
```

Three upstreams are routinely flaky; learn the steady-state rates so you only flag departures from baseline:

| Upstream | Steady-state | Flag when | Impact |
|---|---|---|---|
| **Open-Meteo** (`Failed to fetch metadata for ecmwf/gfs/icon ...: Server error '502'`) | 0–3 per hour | >5/h, or any **sustained** failures across multiple models simultaneously | Briefing falls back to remaining models. Multi-model concurrent failure → check our egress. |
| **AvWx / aviationweather.gov** (`AvWx fetch failed for metar/taf: ... Read timed out`) | 10–25 per day | >5/h sustained | US METAR/TAF unavailable for that briefing — non-fatal for European routes. |
| **DWD charts** (`DWD chart fetch failed (NNN): ... dwd.de ... Read timed out`) | 0–5 per day | >10/day | DWD frontal charts missing from briefing — non-fatal. Briefing still ships. |

`Failed to fetch <model>` (without "metadata for") is the actual data-pull failure, not the catalogue probe — that's more serious and should always be flagged.

## 9. Storage & retention

`/mnt/flyfun_data` is the canonical disk gauge (**199 GB volume** — resized up from 149 GB; don't cite the old number). Steady-state usage is **~62–78 %**, re-baselined 2026-07-28 at **66 % (124 GB used, 67 GB free)** — down from 71 %/134 GB the day before, which is ordinary ICON cache churn, not a leak. See §L9 for the composition breakdown. Only flag when:
- Sustained **>85 %** (real warn — ~169 GB, within ~30 GB of full)
- Sustained **>90 %** (issue — ~179 GB, briefings will start failing soon)

The band oscillates by design as **ICON-EU** (12 h TTL) and **ICON-D2** (6 h TTL, 8 runs/day) runs cycle through. A 4 pp jump in 12 h is normal cache churn, not a leak.

### 9a. Disk composition (one-liner sweep)

```bash
ssh brice@161.35.35.15 "du -sh /mnt/flyfun_data/* 2>/dev/null | sort -h && \
  echo --- && du -sh /mnt/flyfun_data/weather/data/.??* /mnt/flyfun_data/weather/data/*/ 2>/dev/null | sort -h && \
  echo --- && du -sh /mnt/flyfun_data/weather/data/.cache/grib/*/ 2>/dev/null"
```

Note the `.??*` glob — the `.cache` dir is **dotfile-hidden** and a plain `du -sh /mnt/flyfun_data/weather/data/*` will silently miss the ~70 GB of GRIB cache that lives there (ICON-D2 + ICON-EU + GFS).

Compare against the expected bands in §L9. **Flag the line item that's out of band**, not just total usage.

### 9b. Pack growth (the actual long-term driver)

GRIB caches and ECMWF deliveries rotate; **only `packs/` and `mysql/` grow monotonically**, so those are the budget you actually need to track.

```bash
ssh brice@161.35.35.15 "du -sh /mnt/flyfun_data/weather/data/packs && \
  ls /mnt/flyfun_data/weather/data/packs | wc -l && echo pack_flight_dirs && \
  find /mnt/flyfun_data/weather/data/packs -maxdepth 2 -type d -mtime +30 2>/dev/null | wc -l && echo dirs_older_than_T1_threshold"
```

Pack retention: `RETENTION_T1_DAYS=30` (strip heavy artifacts), `RETENTION_T2_ACTIVE_DAYS=180` / `RETENTION_T2_INACTIVE_DAYS=90` (delete). Read:
- Total size + flight count to gauge per-flight average (~100 MB / flight is normal).
- `dirs_older_than_30d` should be growing modestly; if it grows without `packs/` total shrinking, T1 stripping isn't recovering bytes (or there's nothing heavy left to strip).

**Headroom math:** subtract fixed components from the **199 GB** volume to get the pack/mysql budget (2026-07-27 observation):
- Caches (ICON-D2 33 + ICON-EU 30 + GFS 4.8 + ECMWF 13 + SRTM 4.4): **~85 GB** steady-state
- MySQL (data + binlogs): ~12 GB
- Other (sandboxes, forms, logs): ~1 GB
- → **Pack budget before 85 % warn (169 GB): ~71 GB**, before 90 % issue (179 GB): **~81 GB**

So at today's 37 GB / 504 flight-dirs (~73 MB/flight — T1 stripping is recovering bytes, the old ~100 MB/flight figure has come down), we're at ~52 % of the warn budget.

**Worst-case caveat:** that math assumes caches at their *observed* 85 GB. Their *ceiling* is much higher — ICON-D2 is capped at 45 GiB (~48 GB) and ICON-EU can reach 50 GB, which would put caches near 130 GB and shrink the pack budget to ~25 GB. If both caches sit high simultaneously, disk gets tight much faster than the steady-state number suggests. Check the per-cache lines before trusting the headroom figure.

### 9c. MySQL size + binlog growth

MySQL is split between (a) actual app data and (b) binary logs. Treat them separately:

```bash
ssh brice@161.35.35.15 \
  'docker exec shared-mysql sh -c "du -sh /var/lib/mysql/weatherbrief && \
   ls -lh /var/lib/mysql/weatherbrief/*.ibd 2>/dev/null | sort -k5 -h | tail -6 && \
   echo --- && du -ch /var/lib/mysql/binlog.0000* 2>/dev/null | tail -1 && \
   echo --- oldest_binlog && ls -lt /var/lib/mysql/binlog.0000* | tail -1"'
```

Note: glob `binlog.0000*` (not `binlog.*`) — the unqualified glob picks up `binlog.index` (a 4 KB metadata file) and skews any `tail -1`.

Expected (re-baselined 2026-07-18):
- `weatherbrief` DB **4–6 GB** — dominated by `verification_scores.ibd` (~2.7 GB data+indexes at 7.6M rows, growing ~65K scores/day), then `verification_observations.ibd` (~740 MB), `verification_daily_stats.ibd` (~310 MB), `airport_forecast_snapshots.ibd` (~270 MB). Anything else >100 MB is unusual.
- **Binary logs ~10 GB across ~10 files** of 1.1 GB each, spanning ~30 days. They auto-rotate at the configured `binlog_expire_logs_seconds` (default 30 days = 2592000 s).
- Sibling DBs (wordpress_roz, flyfunboarding): tens of MB, ignore.

**Flag when:**
- Binlogs span >40 days (expiry not running) — `PURGE BINARY LOGS BEFORE '<date>'` or set `binlog_expire_logs_seconds` shorter.
- `weatherbrief` DB >8 GB — investigate which table grew (likely `verification_scores` if forecast cycle ran longer/more models).
- A single `.ibd` file doubles between checks without an obvious cause (new feature, new model).

### 9d. Retention loop running?

```bash
ssh brice@161.35.35.15 "docker logs --since 48h weatherbrief 2>&1 \
  | grep -E 'Retention applied:|Purged .* GRIB cache|Age-evicted .* DWD|Raw retention: pruned|Retention cycle failed|GRIB cache purge failed|ECMWF delivery purge failed'"
```

**Important — read carefully**:
- `Retention applied: T1=N packs, T2=M packs, freed=X.X MB` is **only about briefing packs in the DB**, not the GRIB caches. `freed=0.0 MB` is **fine** when no pack crossed a TTL boundary in this window — it does NOT mean retention is broken.
- `Purged N old <model> GRIB cache dirs` is the GRIB cache log line. The code **only logs when `N > 0`** (`if removed:`). So a quiet purge = a successful no-op cycle, indistinguishable from "didn't run". To verify the loop is alive, check for `Retention applied:` (which always logs) — the GRIB purge runs in the same pass.
- `Retention cycle failed` / `GRIB cache purge failed` / `ECMWF delivery purge failed` — any of these is a real issue; the pass aborted mid-way.

If `Retention applied:` is missing for >36 h, the loop is wedged. Otherwise retention is doing its job even when `freed=0`.

---

## Known lessons (apply before flagging)

### §L1 — Low CPU + slow stage = GIL / IPC, not cores
If `Pipeline timing:` is slow but DO CPU is well below `100% × n_cpus` and load is below `n_cpus`, do **not** suggest adding workers or cores. The decode pool is already a `ProcessPoolExecutor`; the next levers are vectorisation or scheduling (don't overlap forecast cycle with peak user time). Reference: 2026-05-02 droplet capture (32 % CPU, load 1.37, 3–6× slower decode).

### §L2 — cgroup ≠ memory pressure for the standalone cycle (RSS is what matters)
**Headline:** `peak_cgroup` will almost always pin near the cgroup limit during a standalone forecast cycle. That is **not** an OOM risk signal on its own — it's the kernel doing its job.

Why: the cycle does heavy compute on GRIB files via cfgrib's mmap path. The kernel page cache fills opportunistically with those file-backed pages, all the way up to the cgroup limit. Those pages are *reclaimable* (file-backed, clean) — when the next allocation needs anonymous memory, the kernel evicts them with no swap or OOM impact. So `peak_cgroup` is roughly `peak_rss + (whatever file cache fits before the limit)`, which is a function of the *limit*, not the workload's actual demand.

The real demand signal is **`peak_rss`** (anonymous + actually-resident pages). That's what we log alongside `peak_cgroup` in `Standalone cycle peaks: rss=<N>MB cgroup=<N>MB`.

How to apply:
- Look at `peak_rss` against the 6 GiB cgroup for risk assessment, not `peak_cgroup`.
- Healthy band for `peak_rss` during a forecast cycle: up to ~3.5 GiB. >4.5 GiB is a real warn.
- The app's `Standalone cycle memory anomaly` alert currently compares `peak_cgroup` to an absolute threshold (~4915 MB on a 6 GiB cgroup). That threshold will fire routinely on cycles doing real GRIB work, so **don't treat the alert firing as automatically a problem** — drill in to `peak_rss` to decide. (Fixing the alert to threshold on RSS instead of cgroup is a separate cleanup; until then, this skill is the interpretation layer.)
- Where this *does* matter: if `peak_rss` is also high (say >4.5 GiB) and a user refresh lands mid-cycle, the cgroup has no room left to absorb the user's allocations even after page-cache reclaim — that's the genuine OOM risk path documented in [[project_standalone_concurrency_tuning]].
- Container swap reported per-pid (`VmSwap` in `Memory after ...` log lines) is a better "is the cgroup actually under pressure" signal than container mem usage — sustained tens-to-hundreds of MB swap *inside the container* is real pressure; the host's 2 GiB host-level swap can also include mmap pages from other workloads and is a less reliable signal.

### §L3 — "Pool stuck" is the recovery path
`GRIB decode pool stuck (worker hung 300s on <fn>); resetting` is the **fix** firing, not the bug. The pool shuts down with `wait=False` and the next call rebuilds it. A handful per day is acceptable. A burst is the signal — correlate with droplet metrics and disk I/O.

### §L4 — RSS high-water marks drift
Peak RSS climbing 1846 → 2619 MB over 24 requests on a fixed cgroup is the **expected** high-water-mark random walk on Python heap fragmentation, not a leak. A leak signature is monotonic climb of *baseline* (`start` checkpoint) values across many requests, with no GC reclaims. Don't propose nightly restarts — see [[project_periodic_restart_deferred]]. Revisit only if peak crosses ~5.5 GiB on the 6 GiB cgroup, or deploy frequency drops below 1 / week.

### §L5 — Don't suggest droplet downgrade
The 4 vCPU / 7.8 GB droplet was upgraded based on a miscalculation; downgrade requires a full rebuild and won't happen. Frame extra capacity as headroom for new work, not waste. The real cost levers are LLM digest spend and ECMWF order tier. See [[project_droplet_sunk_cost]].

### §L6 — Scan traffic is noise
Lines like `GET /joomla/.env`, `error_log.php`, `wp-login.php`, `.git/`, `xmlrpc` are random attack scans hitting the front door. Filter them out of any "errors" count; mention only if the rate is unusually high (e.g. ratelimit something).

### §L7 — Cycle hours overlap with user peak
06:15 / 09:15 / 12:15 / 15:15 / 18:15 UTC are the **light** cycle hours (short since #448). Load spikes and slower briefings *during* these windows are expected — a briefing taking 2× normal at 09:20 UTC is not the same problem as one taking 2× at 02:00 UTC.

**Superseded 2026-07-28:** this lesson used to say the heavy forecast cycle holds a droplet core for the better part of an hour at 07:15 / 19:15. That work moved off-box (see §6) — the droplet now only *imports* the node's artifact (~72 s), so those two hours are no longer a load excuse on the droplet. Load above the 4-vCPU line outside a light-cycle hour is most often concurrent user briefings; check §4b timings before assuming a cycle. Observed 2026-07-28: load 1m peak 5.02 in a window with no cycle at all and 7 briefings in flight.

### §L8 — `docker compose` v2, not `docker-compose`
On this droplet the binary is `docker compose` (two words). Don't try `docker-compose`.

### §L9 — Disk composition (steady-state vs growing)
`/mnt/flyfun_data` is **199 GB** (resized up from 149 GB) and lives in two categories — **rotating** (bounded by TTL) and **growing** (bounded only by retention / nothing):

| Component | Path | Type | Expected band | Notes |
|---|---|---|---|---|
| **ICON-D2 GRIB cache** | `.cache/grib/icon-d2/` | rotating | **30–45 GB** (obs. 33 GB) | **6 h TTL, 8 runs/day** (every 3 h). Hard disk cap **45 GiB** (`_DEFAULT_CACHE_CAP_GIB`, issue #475), oldest-init-first eviction with a 2-run floor so current+prior are never evicted. Override via `WB_GRIB_CACHE_CAP_GB_ICON_D2`. Mind units: the cap is binary GiB, `du` reports decimal GB. Largest single component on the volume — check it first when disk moves. |
| ICON-EU GRIB cache | `.cache/grib/icon-eu/` | rotating | **30–50 GB** (obs. 30 GB) | 12 h TTL × ~4 runs/window × ~10 GB/run. Uncapped. Logs `Purged N old icon-eu GRIB cache dirs` only when N>0. |
| GFS GRIB cache | `.cache/grib/gfs/` | rotating | **1–5 GB** (obs. 4.8 GB) | 24 h TTL × ~5 runs × ~0.5–1 GB/run. Uncapped (cheap). |
| ECMWF deliveries | `/mnt/flyfun_data/ecmwf/data` | rotating | **13–25 GB** (obs. 13 GB) | 36 h TTL via `purge_old_ecmwf_deliveries`. |
| SRTM terrain | `.cache/srtm/` | constant | **~4.4 GB** | Never aged. |
| Pack store | `packs/` | growing | **see headroom math §9b** (obs. 37 GB / 504 dirs) | Monotonic until T1 strip (30 d) and T2 delete (90/180 d) kick in. |
| MySQL data | `mysql/<db>/*.ibd` | growing | **4–6 GB (weatherbrief DB, 2026-07 baseline)** | Dominated by `verification_scores` (~2.7 GB); grows with verification cycle output. |
| MySQL binlogs | `mysql/binlog.0000XX` | rotating | **~10 GB (~10 files × 1.1 GB, ~30 d span)** | Auto-purged at `binlog_expire_logs_seconds`. |
| Sandboxes / forms / logs | misc | constant | **<1 GB combined** | Ignore unless growing. |

**Apply this to §9:** the skill's job is to flag the *individual component* that's out of band, not the total. A total of 71 % isn't a warn if every component is in its expected band — it's just the sum of healthy rotating caches plus the slowly-growing pack store. The total only becomes a warn when a component breaks out (e.g. binlogs span >40 d, or `packs/` > headroom budget).

**The two GRIB caches together are the swing factor.** ICON-D2 (≤45 GiB capped) + ICON-EU (uncapped, up to ~50 GB) can jointly range from ~60 GB to ~98 GB. That ~38 GB swing is larger than the entire pack store, so a total-usage jump is far more likely to be cache phase than real growth. Always break the total down before concluding anything.

Historical notes:
- 2026-05-19: `/mnt/flyfun_data` at 79 % of the then-149 GB volume, all components in-band. The "growth" was just ICON-EU cycling 00z→12z runs.
- 2026-07-27: re-baselined after the volume resize to 199 GB and the arrival of the ICON-D2 cache. 134 GB used (71 %), every component in band.
- 2026-07-28: 124 GB used (66 %) — 10 GB *lower* than the day before, purely ICON cache phase. Confirms the swing described above and is the reason the band's floor was widened from 68 % to 62 %. A drop is as unremarkable as a rise; neither is a signal on its own.

```
verdict: ok            ← or `warn` / `issue`
prod healthy over the last <window>; <one-sentence headline of anything notable>

droplet:    cpu peak X% avg Y%, mem peak Z%, load 1m peak L         (ok)
container:  weatherbrief healthy <up Xh>, X.X GiB / 6 GiB            (ok)
refresh:    A active, Q queued, p95 elapsed Ns                       (ok|note|warn)
pipeline:   N briefings in window, slowest Ns                        (ok|note)
standalone: last cycle <time> ago, T min, peak_rss <N> MB            (ok|warn)
grib pool:  K resets in window                                       (ok|note)
rss:        no new HWM steps   (or "+N MB step at <ts>")             (ok|note)
upstream:   M Open-Meteo 502s, K AvWx, L DWD                         (ok|note)
disk total: /mnt/flyfun_data X% of 199 GB (band ~62–78%)             (ok|warn)
caches:     icon-d2 N GB, icon-eu N GB, gfs N GB, ecmwf N GB         (ok|warn if out-of-band)
growing:    packs N GB / F flights, mysql data N GB, binlogs N GB    (ok|note|warn)
retention:  Retention applied (last <time>); GRIB purge alive        (ok|warn if missing)

what to look at (only if verdict != ok):
- <specific bullet pointing at a log line, container, or grep result>
- ...
```

Keep it terse. The user wants signal, not a dashboard dump.
