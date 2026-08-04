---
name: check-health
description: End-to-end production health check for weatherbrief on the flyfun.aero droplet — droplet metrics, container state, log signals, refresh queue, standalone cycle, RSS growth
disable-model-invocation: true
---

# Production health check

Use when the user asks "is prod ok?", "check droplet health", "anything broken?", or
before/after a deploy as a smoke check. Pull the data, interpret it through
`designs/references/production-health.md`, then report a short verdict — not a wall of raw
output.

**Default window:** last 1 hour for live triage, last 24 h for a daily look. Take an argument
if the user gives one (e.g. "since 07:00 UTC").

## Before you start

Resolve `<user>@<server>`, `<data-volume>`, `<shared-infra-dir>` and `<project-dir>` per
`designs/references/deployment-paths.md` — once, at the start, then reuse. That doc has a
one-shot snippet that pulls all of them.

**`<data-volume>` is the mount point, not `HOST_DATA_DIR`.** Every disk band in §9 gauges the
volume; the data dir lives a couple of levels inside it. Derive it:
`ssh <user>@<server> "df -P '<HOST_DATA_DIR>' | tail -1 | awk '{print \$6}'"`.

Interpretation data lives in `designs/references/production-health.md`. **Read the sections you
need, not the whole file** — a targeted check usually needs two or three:

| Doing | Read |
|---|---|
| Any section — before flagging *anything* | "Thresholds at a glance" |
| §1 droplet metrics, §4b slow pipeline | §L1 (low CPU + slow = GIL, not cores) |
| §2 container memory, §6 cycle peaks | §L2 (cgroup ≠ pressure; read `rss=`) |
| §2 `<mysql-container>` looks small | §L10 (swapped-out buffer pool reads low) |
| §3 error triage | "Log-noise tiers", §L6 (scan traffic) |
| §5 GRIB pool | §L3 ("pool stuck" is the fix firing) |
| §6 standalone cycle | "Standalone cycle detail" |
| §7 RSS growth | §L4 (high-water marks drift; not a leak) |
| §8 upstreams | "Upstream steady-state rates" |
| §9 disk | §L9 (composition + headroom math) |
| §9 retention looks silent | §L11 (silence ≠ failure) |
| §10 MySQL config / counters / connections | §L12 (lifetime ≠ rate; drift is an incident) |
| §10 slow query log | §L13 (shared instance; filename + rotation traps) |
| Tempted to suggest more cores / a smaller droplet | §L1, §L5 |

Several routine conditions look alarming, so check the relevant lesson **before** flagging.

## How to run

Step through the sections in order. Run independent checks in parallel where you can — most
are read-only and fast. Then produce **one** summary block (format at the bottom). Don't dump
raw logs or full metric tables; the user wants the synthesis.

## 1. Droplet metrics

```bash
python3 ~/.claude/skills/droplet-metrics/metrics.py --minutes <window>
```

Read memory peak_used %, load 1m peak vs vCPUs, CPU avg vs peak, FS used_peak, net peak
against the threshold table in the reference. Two traps: *low CPU + high latency* is the
GIL/IPC signature (§L1), not a cores problem; and load peaks during a light-cycle hour are
expected (§L7).

Disk I/O is **not** exposed by the DO API. If CPU and load look fine but something is slow,
ssh in and run `iostat -xm 5 3` to rule out disk before assuming GIL.

## 2. Container state + host pressure

```bash
ssh <user>@<server> "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' && echo --- \
  && docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' \
  && echo --- && free -h && echo --- && uptime \
  && echo --- && df -h / <data-volume>"
```

Check all containers are `(healthy)`, and read `weatherbrief` memory against the 6 GiB cgroup.

For `<mysql-container>`, read §L10 before concluding anything from RSS — a swapped-out buffer
pool reads low while the config is still correct. Confirm the setting with the credential-safe
form from §10 (don't put the password on the command line):

```bash
ssh <user>@<server> "docker exec <mysql-container> sh -c \
  'mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" -t -e \"\$1\"' _ \
  \"SELECT @@global.innodb_buffer_pool_size\""
```

Full config-drift checking lives in §10 — this is just the one value worth having early.

For host swap, attribute it before judging (§L2, §L10) — production parks mmap-backed cfgrib
pages there under pressure, and cold pages accumulate on a long-uptime box:

```bash
ssh <user>@<server> "for f in /proc/*/status; do \
  awk '/^Name:/{n=\$2}/^VmSwap:/{if(\$2>50000) print \$2\" kB  \"n}' \$f 2>/dev/null; \
  done | sort -rn | head -12"
```

Single-digit MB free Mem is fine if buff/cache is large — what matters is `available`.

## 3. Recent errors & warnings

```bash
ssh <user>@<server> \
  "docker logs --since <window> weatherbrief 2>&1 \
   | grep -iE 'error|exception|traceback|killed|oom|fatal|critical|broken|stuck|warning' \
   | grep -vE '\.env HTTP|error_log\.php|joomla|wp-admin|wp-login|/\.git/|xmlrpc|phpinfo|setup\.php' \
   | head -80"
```

The second grep strips scan noise (§L6) — never include it in the verdict. Bucket what's left
into the three tiers in the reference (real errors / self-alerts / recurring upstream noise).
Quick bucketing once you have the filtered log:

```bash
... | awk -F: '{print $1"  "$2}' | sort | uniq -c | sort -rn | head -10
```

`journalctl CONTAINER_NAME=weatherbrief` survives rebuilds if you need history past a deploy.

## 4. Refresh queue & briefing pipeline

**4a. Live queue.** The app exposes `/api/admin/metrics` (auth required). If the user hasn't
supplied an admin token, skip this and rely on the log greps — don't fish for a token on disk.

```bash
ssh <user>@<server> \
  "curl -s -H 'Cookie: flyfun_auth=<admin-token>' \
   http://127.0.0.1:8020/api/admin/metrics | python3 -m json.tool | head -40"
```

0–2 `active_refreshes` is normal; persistently >0 `queued` means the cap is biting. Flag
`p95_elapsed` >180 s or `p95_queue_wait` >30 s.

**4b. Pipeline timing.**

```bash
ssh <user>@<server> "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'Pipeline timing:|Auto-refresh (completed|failed|skipping)|Background refresh complete|QueueFullError|Briefing refresh queued'"
```

- `Pipeline timing: ... total=<N>s` — diagnose by dominant stage: `fetch=` → upstream;
  `analyze=` → GIL/decode contention (§L1, §L3); `llm_digest=` → LLM latency (35–45 s is
  steady state, >60 s suggests oversized context); `dwd_charts=` (~30 s) → dwd.de timing out.
- `Auto-refresh: skipping <id> (refresh already in progress)` — one is fine, bursts mean a
  stuck worker.
- `Auto-refresh failed for <id>` — always investigate, traceback follows.
- `QueueFullError` / `Refresh queue full (N active)` — flag with the count.

## 5. GRIB ProcessPool

```bash
ssh <user>@<server> "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'GRIB decode pool stuck|BrokenProcessPool|GRIB2 enrichment:|No .* GRIB2 data retrieved'"
```

`pool stuck ... resetting` is the recovery path firing, not the bug (§L3). `BrokenProcessPool`
in a traceback is a worker SIGKILL/OOM — cross-reference RSS at that timestamp. `No <model>
GRIB2 data retrieved` is usually transient; flag only on repeated failures across cycles.

## 6. Standalone verification cycle

Schedule: **light** cycles at 06:15 / 09:15 / 12:15 / 15:15 / 18:15 UTC, **METAR ingest**
every 30 min, and the heavy **forecast** work at 07:15 / 19:15 UTC — which runs on a compute
node, not the droplet. See the reference's "Standalone cycle detail" section for the three
flavours, the `(pooled)` check, cache-rebuild breakdown, and the equivalence check.

Ground truth is the DB, not the logs (the import is quiet in `docker logs`):

```bash
ssh <user>@<server> "docker exec weatherbrief python -c \"
from weatherbrief.db import get_engine
from sqlalchemy import text
with get_engine().connect() as c:
    for r in c.execute(text('''SELECT source, COUNT(*), MAX(started_at), AVG(duration_ms)/1000
        FROM verification_cycles WHERE started_at > UTC_TIMESTAMP() - INTERVAL 3 DAY
        GROUP BY source ORDER BY 2 DESC''')): print(r)
\""
```

Expect `standalone_imported` ~2/day at 60–90 s. **If it goes stale, the compute node stopped
delivering** — check it is reachable and that its cycle ran.

Then grep the cycle's footprint:

```bash
ssh <user>@<server> "docker logs --since 24h weatherbrief 2>&1 \
  | grep -E 'Standalone .* cycle:|Standalone cycle peaks:|Standalone cycle RSS @|Standalone cycle memory anomaly|Recorded failed .* cycle|Memory anomaly check failed|ECMWF a. decode failed|Cache rebuild:|ECMWF GRIB leg:|get_digest_data|analyzed [0-9]+ snapshots|\(pooled\)|GRIB decode pool (started|shut down)|force-terminated'"
```

On `Standalone cycle peaks: rss=<N>MB cgroup=<N>MB` — **read `rss=`, not `cgroup=`** (§L2).
On the `Standalone cycle memory anomaly` self-alert, §L2 again: it thresholds on `peak_cgroup`
and fires routinely without indicating a problem. Drill into `peak_rss` to decide.

## 7. RSS growth & memory hygiene

```bash
ssh <user>@<server> "docker logs --since 24h weatherbrief 2>&1 \
  | grep -E 'Memory high-water mark crossed|Memory curve:|Memory after .*: rss=|MemorySampler tick failed'"
```

High-water-mark steps fire every 500 MiB crossed; two or three in 24 h is fine, a steady climb
with no plateau is a leak signature — but read §L4 first, the random walk is expected.
`MemorySampler tick failed` is a note if it repeats.

## 8. Model & chart upstream health

```bash
ssh <user>@<server> "docker logs --since <window> weatherbrief 2>&1 \
  | grep -E 'Failed to fetch metadata for|Failed to fetch .*: |returned no values|AvWx fetch failed|DWD chart fetch failed'"
```

Three upstreams are routinely flaky — compare against the steady-state rate table in the
reference so you only flag departures from baseline.

## 9. Storage & retention

```bash
ssh <user>@<server> "du -sh <data-volume>/* 2>/dev/null | sort -h && \
  echo --- && du -sh <data-volume>/weather/data/.??* <data-volume>/weather/data/*/ 2>/dev/null | sort -h && \
  echo --- && du -sh <data-volume>/weather/data/.cache/grib/*/ 2>/dev/null"
```

Note the `.??*` glob — `.cache` is dotfile-hidden and a plain `*` silently misses ~70 GB of
GRIB cache. Compare each line against the §L9 composition table and **flag the component
that's out of band, not the total**.

Pack growth (the actual long-term driver — only `packs/` and `mysql/` grow monotonically):

```bash
ssh <user>@<server> "du -sh <data-volume>/weather/data/packs && \
  ls <data-volume>/weather/data/packs | wc -l && echo pack_flight_dirs && \
  find <data-volume>/weather/data/packs -maxdepth 2 -type d -mtime +30 2>/dev/null | wc -l && echo dirs_older_than_T1"
```

Retention: `RETENTION_T1_DAYS=30` (strip heavy artifacts), `RETENTION_T2_ACTIVE_DAYS=180` /
`RETENTION_T2_INACTIVE_DAYS=90` (delete). If `dirs_older_than_T1` grows without `packs/` total
shrinking, T1 stripping isn't recovering bytes. Use the §L9 headroom math for the budget.

MySQL size + binlogs (treat data and logs separately):

```bash
ssh <user>@<server> \
  'docker exec <mysql-container> sh -c "du -sh /var/lib/mysql/weatherbrief && \
   ls -lh /var/lib/mysql/weatherbrief/*.ibd 2>/dev/null | sort -k5 -h | tail -6 && \
   echo --- && du -ch /var/lib/mysql/binlog.0000* 2>/dev/null | tail -1 && \
   echo --- oldest_binlog && ls -lt /var/lib/mysql/binlog.0000* | tail -1"'
```

Glob `binlog.0000*`, not `binlog.*` — the unqualified glob picks up `binlog.index` (4 KB) and
skews any `tail -1`. Flag when binlogs span >40 days, the app DB exceeds 8 GB, or a single
`.ibd` doubles between checks without an obvious cause.

Retention loop alive?

```bash
ssh <user>@<server> "docker logs --since 48h weatherbrief 2>&1 \
  | grep -E 'Retention applied:|Purged .* GRIB cache|Age-evicted .* DWD|Raw retention: pruned|Retention cycle failed|GRIB cache purge failed|ECMWF delivery purge failed'"
```

Read §L11 carefully before calling retention broken — `freed=0.0 MB` and a silent GRIB purge
are both normal, and `Retention applied:` is the line that proves the loop ran.

## 10. MySQL config & behaviour

The instance is **shared** across every app on the droplet, so everything here is
cross-app: a change made for weatherbrief can break WordPress, and a slow query from
WordPress lands in the same log. `<mysql-container>` and the expected values come from
`deploy/mysql-baseline.json` (gitignored; `deploy/mysql-baseline.example.json` documents
the shape). Read §L12 and §L13 before flagging anything.

**Credentials: never pass the password on a command line.** Read it from the container's
own environment — this form keeps it out of your shell history, the process list and the
log of this session:

```bash
ssh <user>@<server> "docker exec <mysql-container> sh -c \
  'mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" -t -e \"\$1\"' _ \"<SQL>\""
```

**10a. Config drift — the highest-value check here.**

```sql
SELECT @@global.innodb_buffer_pool_size, @@global.innodb_redo_log_capacity,
       @@global.innodb_io_capacity, @@global.innodb_io_capacity_max,
       @@global.max_connections, @@global.slow_query_log,
       @@global.long_query_time, @@global.slow_query_log_file;
```

Compare every value against `expected_variables` in the baseline file. **`innodb_buffer_pool_size`
reading 134217728 (128 MB, the stock default) is an incident, not a note** — it means the
persisted config was lost, and the verification cache rebuild goes from ~90 s toward ~40 min
(§L12). Ignore a mismatch on anything listed under `known_pending` in the baseline —
`PERSIST_ONLY` settings deliberately differ from the running value until the next start.

Non-default values live **only** in `mysqld-auto.cnf` inside the datadir, which is on the data
volume and in no repo. Confirm what is actually persisted:

```bash
ssh <user>@<server> "docker exec <mysql-container> cat /var/lib/mysql/mysqld-auto.cnf" \
  | python3 -m json.tool
```

Check uptime in the same pass — a restart is what turns a latent config gap into a live one,
and it also zeroes every counter below:

```sql
SHOW GLOBAL STATUS LIKE 'Uptime';
```

If uptime is short and you didn't expect a restart, verify 10a **before** reading anything else.

**10b. Slow query log.**

Confirm it is on, then read it. The filename must be an explicit stable path; if
`slow_query_log_file` matches `<hostname>-slow.log` the deployment is exposed to §L13.

```bash
ssh <user>@<server> "docker exec <mysql-container> sh -c \
  'tail -c 20000 /var/lib/mysql/slow.log' " | grep -E '^# (Time|Query_time)|^SELECT|^INSERT|^UPDATE'
```

Or rank fingerprints: `docker exec <mysql-container> mysqldumpslow -s t -t 20 /var/lib/mysql/slow.log`.

What matters is **what is new**, not the volume. Compare against
`expected_slow_log_fingerprints.known` in the baseline and report only unrecognised entries.
Two standing exclusions: the nightly `mysqldump` (identify by `SQL_NO_CACHE`) and other apps'
queries — this is the shared instance. Also note nothing rotates this file (§L13).

**10c. Connections.**

```sql
SHOW GLOBAL STATUS LIKE 'Connection_errors_max_connections';
SHOW GLOBAL STATUS LIKE 'Max_used_connections';
SHOW GLOBAL STATUS LIKE 'Threads_connected';
```

`Connection_errors_max_connections` above the baseline means the ceiling was hit since the
last check. **Attribute it before acting — it is usually not this app** (§L12). weatherbrief
pools and holds a handful:

```sql
SELECT USER, HOST, CURRENT_CONNECTIONS, TOTAL_CONNECTIONS
  FROM performance_schema.accounts WHERE USER IS NOT NULL
 ORDER BY TOTAL_CONNECTIONS DESC;
```

`performance_schema` keeps no history, so this catches a standing consumer, not a past spike.

**10d. InnoDB pressure.**

```sql
SHOW GLOBAL STATUS LIKE 'Innodb_log_waits';
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_wait_free';
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads';
```

`Innodb_log_waits` should be **0**; non-zero means the redo log is a bottleneck. The other two
are **lifetime totals and are large for historical reasons** — judge them by rate across the
window, never by the absolute number (§L12). Subtract the baseline, divide by elapsed uptime.

## Output

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
disk total: <data-volume> X% of 199 GB (band ~62–78%)                (ok|warn)
caches:     icon-d2 N GB, icon-eu N GB, gfs N GB, ecmwf N GB         (ok|warn if out-of-band)
growing:    packs N GB / F flights, mysql data N GB, binlogs N GB    (ok|note|warn)
retention:  Retention applied (last <time>); GRIB purge alive        (ok|warn if missing)
mysql:      config matches baseline, uptime Nd, slow log N new       (ok|warn|issue)

what to look at (only if verdict != ok):
- <specific bullet pointing at a log line, container, or grep result>
```

Keep it terse. The user wants signal, not a dashboard dump.
