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
- Host **Swap used** — anything more than a few hundred MB swap in use is a warn. The droplet has 4 GiB swap; production sometimes parks 1–2 GiB of mmap-backed cfgrib pages there under memory pressure (see lesson §L2 before reflexively flagging).
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

The cycle runs at configured hours (default 06:15, 09:15, 12:15, 15:15, 18:15 UTC). Grep the latest cycle's footprint:

```bash
ssh brice@161.35.35.15 "docker logs --since 24h weatherbrief 2>&1 \
  | grep -E 'Standalone .* cycle:|Standalone cycle peaks:|Standalone cycle RSS @|Standalone cycle memory anomaly|Recorded failed .* cycle|Memory anomaly check failed|ECMWF a. decode failed'"
```

The cycle has **three flavours**, each with its own `Standalone <type> cycle:` summary line. Don't conflate them:
- **light** — verification scoring rollup only (no model fetch). ~20-30 s. Runs every 10 min.
- **forecast** — heavy model fetch (ECMWF / GFS / ICON / UKMO). 15–30 min typical. Runs at the configured hours.
- **verification** / **METAR ingest** — observation pull + scoring.

Read:

- `Standalone <type> cycle: M models, S snapshots, ... (Tms)` — for the **forecast** flavour, >40 min is a warn, >60 min is an issue. For **light**, anything >2 min is unusual.
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

## 9. Retention & disk

```bash
ssh brice@161.35.35.15 "docker logs --since 48h weatherbrief 2>&1 \
  | grep -E 'Retention .*: T1=|Purged .* GRIB cache|Raw retention: pruned'"
```

Retention runs every 24 h. If the line is missing for >36 h, the loop is wedged. `freed=<MB>` should be non-zero on a busy day; zero across multiple cycles means nothing has aged out (could be fine, could be a bug).

`/mnt/flyfun_data` usage is the canonical disk gauge — current healthy band is 60–75 %.

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

### §L7 — Forecast cycle hours overlap with user peak
06:15 / 09:15 / 12:15 / 15:15 / 18:15 UTC are the standalone cycle hours. Load spikes and slower briefings *during* these windows are expected. A briefing taking 2× normal at 09:20 UTC is not the same problem as one taking 2× at 02:00 UTC.

### §L8 — `docker compose` v2, not `docker-compose`
On this droplet the binary is `docker compose` (two words). Don't try `docker-compose`.

---

## Output template

```
verdict: ok            ← or `warn` / `issue`
prod healthy over the last <window>; <one-sentence headline of anything notable>

droplet:    cpu peak X% avg Y%, mem peak Z%, load 1m peak L  (ok)
container:  weatherbrief healthy <up Xh>, X.X GiB / 6 GiB     (ok)
refresh:    A active, Q queued, p95 elapsed Ns                (ok|note|warn)
pipeline:   N briefings in window, slowest Ns                 (ok|note)
standalone: last cycle <time> ago, T min, peak <N> MB         (ok|warn)
grib pool:  K resets in window                                (ok|note)
rss:        no new HWM steps   (or "+N MB step at <ts>")      (ok|note)
upstream:   M Open-Meteo 502s, K decode failures              (ok|note)
disk:       / X%, /mnt/flyfun_data Y%                         (ok|warn)

what to look at (only if verdict != ok):
- <specific bullet pointing at a log line, container, or grep result>
- ...
```

Keep it terse. The user wants signal, not a dashboard dump.
