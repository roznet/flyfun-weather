---
name: live-review
description: Review real flights' live-layer alerts and highlights on prod — pull live_history, check every alert against the raw report behind it, flag regressions of past fixes, scan logs, and optionally replay old vs current classifier and cross-check against radar on the Mac mini. Ad hoc, when the user asks ("check the live flights since Monday").
---

# Live review

The live layer (`designs/live-observation-layer.md`) tells a pilot what changed since the
briefing. **Alerts are scarce by design**: an alert only for a real issue, everything else a
highlight, which is always shown. A review hunts for two failures:
1. **An alert that wasn't a real issue** (false alarm).
2. **A real issue that only got a highlight**, or nothing at all.

A low alert count is success, not a gap.

Read before judging a tier: `designs/meteorology-decisions.md` §34–39, plus the newest
section if the rules changed since.

## 0. Where things are

Resolve every path with the ops tool; never hardcode them:

```bash
eval "$(python3 scripts/ops/hosts.py server --env)"   # SERVER_SSH, HOST_DATA_DIR, SERVER_CONTAINER, SERVER_HEAD
python3 scripts/ops/hosts.py nodes                    # the Mac mini: reachable only on the home LAN
eval "$(python3 scripts/ops/hosts.py local --env)"    # LOCAL_AIRPORTS_DB, LOCAL_VENV
S=.claude/skills/live-review/scripts
W=$CLAUDE_JOB_DIR/tmp/live-review    # or another scratch dir
```

Confirm prod is what you think it is. The container start time and the euro-aip version
catch the "healthy container, old image" deploy race:

```bash
ssh $SERVER_SSH "docker inspect -f '{{.State.StartedAt}}' $SERVER_CONTAINER; \
  docker exec $SERVER_CONTAINER python -c 'import euro_aip; print(euro_aip.__version__)'"
```

## 1. Pull the window

Fetch the live files and the pack `briefing.json` the replay needs. That's about 1–2 MB per
flight; never pull observed frames.

```bash
SINCE="2026-10-06 00:00"
mkdir -p $W/live
ssh $SERVER_SSH "cd $HOST_DATA_DIR/packs && find . -maxdepth 3 \( -name live_history.jsonl \
  -o -name live_meta.json -o -name live.json \) -newermt '$SINCE' | tar czf - -T -" | tar xzf - -C $W/live
$LOCAL_VENV/bin/python $S/review.py briefing-list $W/live \
  | ssh $SERVER_SSH "cd $HOST_DATA_DIR/packs && tar czf - -T -" | tar xzf - -C $W/live
```

## 2. Summarize: every alert, with its raw report

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
  - `REGRESSION§39: en-route CB/TCU alert`: en route, only TS/VCTS should alert.
  - `REGRESSION#682: numeric radar/lightning identity`: a radar or lightning row whose
    identity is a dBZ value or a count. That makes rows churn every tick.
  - `REGRESSION#682: <cat> but prevailing visibility + ceiling give <cat>`: the category came
    from the sector minimum (the second visibility group) instead of the prevailing one.
    `9999 1400` is VFR, not LIFR.

Then judge each alert as a pilot would. SIGMET rows should read "replaces" for a reissue and
"from HH:MMZ" for one not yet valid (#683). Who looked at an alert can be seen from
`/live` requests in the app log (`journalctl CONTAINER_NAME=weatherbrief | grep '/live'`).

## 3. Logs since the deploy or window start

```bash
ssh $SERVER_SSH "journalctl CONTAINER_NAME=weatherbrief --since '$SINCE' --no-pager -o cat" > $W/app.log
grep -c Traceback $W/app.log; grep -E '" 5[0-9]{2} ' $W/app.log | head
grep -E 'Live history write failed|lookahead failed|live tick' $W/app.log | head
grep WARNING $W/app.log | sed -E 's/[0-9]+(\.[0-9]+)?/N/g' | cut -c1-120 | sort | uniq -c | sort -rn | head -20
```

Compare the warning types against a baseline window (the same hours on an earlier day). For
any new pattern, check whether the deploy touched that code (`git diff --stat <old>..<new> -- <path>`)
or whether it's an upstream outage. For example, ICON-D2 404s at 11Z on 2026-10-06 were DWD
not having published yet.

## 4. Optional: replay old vs current classifier

Do this when the classifier changed since those flights flew, or to test a proposed rule.
It re-runs each flight's prod ticks through the current code: the METAR/TAF/SIGMET texts from
the history, with SIGMETs re-dated to their WMO-header issue time.

```bash
$LOCAL_VENV/bin/python $S/review.py replay $W/live $W/replay [--observed $W/observed.json] [FLIGHT_SUBSTR...]
$LOCAL_VENV/bin/python $S/review.py compare $W/live $W/replay [FLIGHT_SUBSTR to detail alert diffs]
```

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

**Replay caveats:**
- A METAR counts as available at its observation time. Prod sees it about 10 min later, so a
  replayed change can land one tick early.
- Flights before the archive start get no radar.

## 5. Optional: did the radar back up a station report?

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

## Report

Lead with the verdict on the fixes and the alerts. Then report:
- the flights reviewed;
- each alert, real or not, with the evidence behind it;
- regressions found;
- what wasn't exercised (e.g. no SIGMET on any route), so it isn't counted as verified;
- new log patterns.

Record notable findings in the memory note `project_live_alerts_push_gating` (the review
log), so the next review knows the baseline.
