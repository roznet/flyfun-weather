---
name: prod-briefings-review
description: Review what production produced over a day or window — pipeline health (refresh jobs, errors, digest latency and cost), live-layer alerts against their raw reports, and a ranked sample of the day's digests reviewed against their exact context, with a running record of findings. Ad hoc, when the user asks ("review prod briefings since Monday", "how did today's digests look").
---

# Prod briefings review

Three parts, run in order for a window (default: today, UTC):

- **A. Pipeline health** — did briefings get built, how fast, at what cost, with what errors.
- **B. Live layer** — every alert and highlight on real flights, against the report behind it.
- **C. Digests** — mechanical checks on every digest, then a review of a ranked sample
  against the exact context the model saw, appended to a running record.

Run all three unless the user asks for one. Parts B and C each stand alone after step 0.

## 0. Where things are

Resolve every path with the ops tool; never hardcode them:

```bash
eval "$(python3 scripts/ops/hosts.py server --env)"   # SERVER_SSH, HOST_DATA_DIR, SERVER_CONTAINER, SERVER_HEAD
python3 scripts/ops/hosts.py nodes                    # the Mac mini: reachable only on the home LAN
eval "$(python3 scripts/ops/hosts.py local --env)"    # LOCAL_AIRPORTS_DB, LOCAL_VENV, LOCAL_DATA_DIR
S=.claude/skills/prod-briefings-review/scripts
W=$CLAUDE_JOB_DIR/tmp/prod-briefings-review    # or another scratch dir
RECORD=$LOCAL_DATA_DIR/reviews/digest_reviews.jsonl  # shared by every worktree; not in git
SINCE_DAY=2026-10-09; UNTIL_DAY=2026-10-10           # UTC dates, UNTIL exclusive
SINCE="$SINCE_DAY 00:00"
mkdir -p $W
scp -q $S/briefings.py $SERVER_SSH:/tmp/ && ssh $SERVER_SSH "docker cp /tmp/briefings.py $SERVER_CONTAINER:/tmp/"
BR="ssh $SERVER_SSH docker exec $SERVER_CONTAINER python /tmp/briefings.py"
```

Confirm prod is what you think it is. The container start time and the euro-aip version
catch the "healthy container, old image" deploy race:

```bash
ssh $SERVER_SSH "docker inspect -f '{{.State.StartedAt}}' $SERVER_CONTAINER; \
  docker exec $SERVER_CONTAINER python -c 'import euro_aip; print(euro_aip.__version__)'"
```

If a deploy landed inside the window, split the comparisons at the container start time.

## A. Pipeline health

```bash
$BR health $SINCE_DAY $UNTIL_DAY
```

Refresh jobs by status and trigger (any `failed`/`abandoned` printed with its stage and
error), packs and digests (`MISSING` must be 0), grades, the digest model split, and token
cost per briefing from the ledger.

Latency comes from the `Pipeline timing` log line; compare against a baseline day:

```bash
ssh $SERVER_SSH "journalctl CONTAINER_NAME=weatherbrief --since '$SINCE' --until '$UNTIL_DAY 00:00' --utc -o cat" > $W/app.log
ssh $SERVER_SSH "journalctl CONTAINER_NAME=weatherbrief --since '<baseline day> 00:00' --until '<next day> 00:00' --utc -o cat | grep 'Pipeline timing'" > $W/baseline.log
python3 $S/briefings.py timing $W/app.log $W/baseline.log
grep -cE '^ERROR|Traceback' $W/app.log
grep WARNING $W/app.log | sed -E 's/[0-9]+(\.[0-9]+)?/N/g' | cut -c1-120 | sort | uniq -c | sort -rn | head -20
```

`llm_digest` is the briefer call alone; `fetch` (mostly the GRIB decode queue) dominates the
total. Treat a new warning pattern as a lead: check whether a deploy touched that code or
whether it is an upstream outage. B3 reuses `$W/app.log`.

## B. Live layer

The live layer (`designs/live-observation-layer.md`) tells a pilot what changed since the
briefing. **Alerts are scarce by design**: an alert only for a real issue, everything else a
highlight, which is always shown. A review hunts for two failures:
1. **An alert that wasn't a real issue** (false alarm).
2. **A real issue that only got a highlight**, or nothing at all.

A low alert count is success, not a gap.

Read before judging a tier: `designs/meteorology-decisions.md` §34–40, plus the newest
section if the rules changed since.

### B1. Pull the window

Fetch the live files and the pack `briefing.json` the replay needs. That's about 1–2 MB per
flight; never pull observed frames.

```bash
mkdir -p $W/live
ssh $SERVER_SSH "cd $HOST_DATA_DIR/packs && find . -maxdepth 3 \( -name live_history.jsonl \
  -o -name live_meta.json -o -name live.json \) -newermt '$SINCE' | tar czf - -T -" | tar xzf - -C $W/live
$LOCAL_VENV/bin/python $S/review.py briefing-list $W/live \
  | ssh $SERVER_SSH "cd $HOST_DATA_DIR/packs && tar czf - -T -" | tar xzf - -C $W/live
```

### B2. Every alert, with its raw report

```bash
$LOCAL_VENV/bin/python $S/review.py summarize $W/live
```

For each flight this prints:
- the event counts;
- every alert, plus every SIGMET, radar, lightning and convective row, each with the raw
  METAR/TAF behind it;
- `!!` flags for regressions of past fixes:
  - `REGRESSION#682: CB/TCU only in trend`: a convective row from a TEMPO/BECMG group
    instead of the observed body.
  - `REGRESSION§41: en-route CB/TCU alert with radar cells up`: en route, a CB/TCU alerts
    only as the fallback (cells feed dark, or no radar coverage at the station), and the row
    then ends "(radar cells unavailable)" / "(no radar coverage there)". TS/VCTS always alerts.
  - `REGRESSION#688: numeric storm identity`: a storm row whose identity carries a number.
  - `REGRESSION#682: numeric radar/lightning identity`: a radar or lightning row whose
    identity is a dBZ value or a count. That makes rows churn every tick.
  - `REGRESSION#682: <cat> but prevailing visibility + ceiling give <cat>`: the category came
    from the sector minimum (the second visibility group) instead of the prevailing one.
    `9999 1400` is VFR, not LIFR.

Then judge each alert as a pilot would. SIGMET rows should read "replaces" for a reissue and
"from HH:MMZ" for one not yet valid (#683). Who looked at an alert can be seen from
`/live` requests in the app log (`journalctl CONTAINER_NAME=weatherbrief | grep '/live'`).

### B3. Live-layer log lines

`$W/app.log` is the window's log from part A (fetch it as there if you skipped A):

```bash
grep -E '" 5[0-9]{2} ' $W/app.log | head
grep -E 'Live history write failed|lookahead failed|live tick|LIVE_HIGHLIGHT' $W/app.log | head
```

For a new pattern, check whether the deploy touched that code (`git diff --stat <old>..<new> -- <path>`)
or whether it's an upstream outage. For example, ICON-D2 404s at 11Z on 2026-10-06 were DWD
not having published yet.

### B4. Optional: replay old vs current classifier

Do this when the classifier changed since those flights flew, or to test a proposed rule.
It re-runs each flight's prod ticks through the current code (`scripts/replay_live_history.py`):
the METAR/TAF/SIGMET texts from the history, with SIGMETs re-dated to their WMO-header issue time.

```bash
$LOCAL_VENV/bin/python $S/review.py replay $W/live $W/replay [--observed $W/observed.json] [--cells $W/cells] [FLIGHT_SUBSTR...]
$LOCAL_VENV/bin/python $S/review.py compare $W/live $W/replay [FLIGHT_SUBSTR to detail alert diffs]
```

`compare` counts alerts, pings, ring rows, storm rows and **flicker** (rows that cleared and
came back) per flight. Without `--cells` the replay has no radar storms, so the §41 fallback
applies: en-route CB/TCU alert again and the ring rows return. That is right for a dark feed,
not for a day the Mini was up — fetch the cells (below) before judging alert counts.

Radar and lightning aren't in the history. Without `--observed`, radar/lightning rows
disappear from the replay, so compare METAR/SIGMET rows only. To include them, sample on the
Mac mini, which holds the observed archive. Run the analysis there and bring back only JSON;
copying frames costs about 1 GB per day over a slow link.

```bash
$LOCAL_VENV/bin/python $S/review.py observed-jobs $W/live $W/jobs.json
scp $W/jobs.json $S/on_mini.py $NODE_SSH:/tmp/
ssh $NODE_SSH "cd /tmp && \$HOME/Developer/public/flyfun-weather/venv/bin/python on_mini.py observed jobs.json observed.json && gzip -f observed.json"
scp $NODE_SSH:/tmp/observed.json.gz $W/ && gunzip -f $W/observed.json.gz
ssh $NODE_SSH "rm -f /tmp/jobs.json /tmp/on_mini.py /tmp/observed.json*"
```

`NODE_SSH` comes from `hosts.py nodes --env`. The Mini's repo must contain
`weatherbrief.observed` at a commit whose payload code matches prod; check with `hosts.py nodes`.
The archive starts 2026-10-03 15:05Z. The archive job moves frames older than 48 h to the
NAS, so replay recent days from the Mini.

The cells display files for `--cells` (and for B6; copy `jobs.json` and `on_mini.py` over as above first), cut to the routes' box, mtime kept so
the replay only reads a file the droplet had by each tick:

```bash
ssh $NODE_SSH "cd /tmp && \$HOME/Developer/public/flyfun-weather/venv/bin/python on_mini.py cells jobs.json cells && tar czf cells.tgz cells"
scp $NODE_SSH:/tmp/cells.tgz $W/ && tar xzf $W/cells.tgz -C $W
ssh $NODE_SSH "rm -rf /tmp/cells /tmp/cells.tgz"
```

Display files stay on the Mini 90 days (`observed-cells.md`), so older flights can be replayed
with cells too.

**Replay caveats:**
- A METAR counts as available at its observation time. Prod sees it about 10 min later, so a
  replayed change can land one tick early.
- Flights before the archive start get no radar.

### B5. Optional: did the radar back up a station report?

Use this when an alert or highlight rests on a station's CB/TCU. It samples radar and
lightning around each METAR's airport over the 10 min before the report.

```bash
$LOCAL_VENV/bin/python $S/review.py metar-points $W/live $W/points.json "2026-10-03T15:15"
scp $W/points.json $S/on_mini.py $NODE_SSH:/tmp/
ssh $NODE_SSH "cd /tmp && \$HOME/Developer/public/flyfun-weather/venv/bin/python on_mini.py airport-radar points.json radar.json"
scp $NODE_SSH:/tmp/radar.json $W/ && ssh $NODE_SSH "rm -f /tmp/points.json /tmp/on_mini.py /tmp/radar.json"
$LOCAL_VENV/bin/python $S/review.py radar-summary $W/radar.json LFBO LFMT
```

**Baseline (2026-10-03..05, 2,523 METARs):** AUTO `///CB` had a heavy echo (≥41 dBZ) within
10 NM 62 % of the time, AUTO `///TCU` 46 %, CB/TCU only in TEMPO 7 %, and METARs without
CB/TCU 1 %.

### B6. Optional: score the storm estimates (#688)

Each tick logs a closest-approach estimate per storm with available motion (`estimate` rows in
`live_history.jsonl`). Score them against what the storm did in later frames (the cells from
B4), by horizon, next to persistence (the storm staying put):

```bash
$LOCAL_VENV/bin/python $S/review.py score-estimates $W/live $W/cells [FLIGHT_SUBSTR...]
```

`lost` counts storms whose lineage ended before the estimated time. The estimate stays in the
storm's detail pop-up, and "closing" stays limited to 30 min in the alert rule (§41), until
this shows the estimate beating persistence at that horizon over several days.

## C. Digests

The briefer (`designs/digest.md`) writes each digest from `digest_context.txt`, which every
pack keeps byte-for-byte, so a digest can be checked against exactly what the model saw.

### C1. Mechanical checks on every digest

```bash
$BR digest-check $SINCE_DAY $UNTIL_DAY
```

Counts, with examples:
- `dwd_duplicated`: the full DWD text pasted under several day labels (the split bug fixed
  in `da9344fd`); should be 0 once that is deployed.
- `first_person`: "our / we / I" in the digest.
- `consensus_over_minority`: a sentence claiming agreement ("across models", "the models
  show") about the subject of a minority-view advisory. A lead, not a finding: read the
  sentence, since real agreement on something else can match.
- `raw_coordinates`: lat/lon leaking into prose.

### C2. Pick the sample

```bash
python3 $S/briefings.py seen $RECORD --days 7 > $W/seen.json
scp -q $W/seen.json $SERVER_SSH:/tmp/ && ssh $SERVER_SSH "docker cp /tmp/seen.json $SERVER_CONTAINER:/tmp/"
$BR sample $SINCE_DAY $UNTIL_DAY --n 12 --seen /tmp/seen.json --out /tmp/briefings_sample.json
$BR export /tmp/briefings_sample.json /tmp/briefings_export.tgz
ssh $SERVER_SSH "docker cp $SERVER_CONTAINER:/tmp/briefings_export.tgz /tmp/ && docker cp $SERVER_CONTAINER:/tmp/briefings_sample.json /tmp/"
mkdir -p $W/digests && scp -q $SERVER_SSH:/tmp/briefings_export.tgz $SERVER_SSH:/tmp/briefings_sample.json $W/ \
  && tar xzf $W/briefings_export.tgz -C $W/digests
```

Per day, `--n` is a ceiling (default 12) and the packs come ranked:
1. Pool: the newest pack per flight, at most two flights per user, long-range outlooks out.
2. Fixed-rule picks, together at most half the ceiling: every 👎, then one each of *softer
   than its worst advisory* (a RED advisory, digest not RED), *harsher than its advisories*,
   *minority-view advisory*, *D-0 observations differ from the models*.
3. Diversity: the pack adding the most features not yet covered — grade, lead bucket,
   locale, pilot capability, DWD mode, each non-GREEN advisory at its colour, each
   minority-view advisory — while it adds at least 2.
4. Lone carriers: a pack with an advisory type no pick has yet.

Those are the **core**. The rest of the ceiling is the **tail**, in gain order. A flight in
`seen.json` (reviewed in the last 7 days) pays a penalty, so it comes back only when it alone
carries something; within one multi-day run, a flight picked on an earlier day is penalised
the same way. Busy days give about 11 core packs, quiet ones about 8.

### C3. Full review of the core

Send the core packs to reviewer subagents, 3–4 packs each, in parallel, with the prompt in
`digest_checklist.md` (the weakness numbers there are what the record counts). Keep the
contexts out of the main session; only the reports come back.

**Check every [major] finding yourself against the raw context before reporting it**:
grep the quoted context line and the digest sentence. A finding that doesn't survive is
dropped.

### C4. Light pass over the tail

Do it in the main session, per `digest_checklist.md` § Light pass: the digest's reason and
headline next to the advisory lines, about 1k tokens a pack. Promote a suspicious pack to a
full review (a subagent, as in C3) and record it as `"tier": "tail", "review": "full"`, so
the record shows over time whether the tail ever pays off.

### C5. Record

Write one entry per reviewed pack (core and tail) to `$W/findings.json`:

```json
[{"day": "2026-10-09", "pack_id": 9448, "flight": "<flight_id>", "tier": "core",
  "review": "full", "grade": "RED", "reason": "<from card.json>", "llm_model": "<from card.json>",
  "findings": [{"weakness": 3, "severity": "major", "note": "GFS-only LIFR written as across models"}]}]
```

```bash
python3 $S/briefings.py record $RECORD $W/findings.json --server-head $SERVER_HEAD
python3 $S/briefings.py tally $RECORD --since <a week or two back>
```

Recording a `(day, pack_id)` again replaces its earlier row, so re-running C5 or recording a
promoted tail pack never double-counts.

`--server-head` is the SHA that wrote the digests: use the pre-deploy SHA for days before a
deploy inside the window. The tally (majors by weakness per day) is how a prompt change is
judged afterwards: one day is noise, a week is a trend.

## Report

Lead with the verdict: what's broken, what regressed, what improved. Then:
- **Pipeline:** jobs, failures, missing digests, model split, digest p50/p90 vs baseline,
  cost per briefing, new log patterns.
- **Live layer:** the flights reviewed; each alert, real or not, with the evidence;
  regressions of past fixes.
- **Digests:** the C1 counts; core/tail sizes; packs with a major, by weakness; the 2–3 worst
  examples with the digest quote and the context line; anything promoted from the tail; the
  tally trend against earlier runs.
- **Not exercised** (e.g. no SIGMET on any route, no 👎 that day), so it isn't counted as verified.

Record live-layer findings in the memory note `project_live_alerts_push_gating`, and the
digest headline (e.g. "minority-view headlines 6/15") in `project_briefer_sonnet55_eval` or
its successor, so the next review knows the baseline.
