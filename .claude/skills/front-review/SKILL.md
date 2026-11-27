---
name: front-review
description: Daily calibration review of the Hewson front detection — pulls the DWD and Met Office surface analyses and the matching Hewson grids from prod, scores our gated front lines against the drawn fronts (boundary found, type right, slope-aware offset by level, why each miss happened) for every model, level and gate variant, checks METAR front passages, records the day with your read of the charts, and keeps a running scoreboard. Use for "/front-review", "review the fronts", "how did the Hewson fronts do today", "front calibration tally".
---

# Front review

The Hewson fronts (`designs/frontal-detection.md`) are calibrated by eye today. This skill
turns each analysis chart into numbers that accumulate, so a gate change is chosen from a
scoreboard, not from one picture. One run covers every analysis prod still has (~2 days):

1. **Pull** charts, trimmed grids and METARs into the case library.
2. **Score** each new analysis time: mechanical, in `score.py`.
3. **Read** the charts and the score plot: the judgement the script can't make.
4. **Record** the scores and your notes.
5. **Tally** and report.

Best run **after ~21Z UTC** (the 12Z Hewson run lands ~20Z, DWD 18Z ~20:30Z) or the next
morning. Prod keeps DWD 8 cycles, Met Office 6, Hewson 4 inits: a skipped day loses
nothing, two do.

## 0. Paths

Each Bash call starts fresh, so put these at the top of every block:

```bash
S=.claude/skills/front-review/scripts
D=$(python3 scripts/ops/hosts.py local --get LOCAL_DATA_DIR)
C=$D/reviews/front_cases            # case library, shared by worktrees, not in git
RECORD=$D/reviews/front_reviews.jsonl
W=${CLAUDE_JOB_DIR:-/tmp}/tmp/front-review; mkdir -p $W   # scratch for notes files
source venv/bin/activate            # score.py imports weatherbrief
```

## 1. Pull

```bash
python3 $S/fetch.py pull $C         # idempotent; --days 2 by default
python3 $S/fetch.py list $C         # what each analysis time has, scored or not
```

Per analysis time T it fetches DWD `ana.png` (+ the 1600 px copy for reading) and the Met
Office `ana.gif` (00/12Z only), the Hewson run initialised at T floored to 00/12Z trimmed
in the container to hours 0–9 plus the full-run background gradient (~4 MB a model), and
the METARs of the day ±6 h. A time whose Hewson run isn't on prod yet prints `unknown` and
is picked up by the next run.

## 2. Score

```bash
python $S/score.py score-all $C --plot     # every unscored time; --rescore after a scorer change
python $S/score.py show $C 2026-11-22T00Z  # the table for one time (--ref mo for Met Office)
```

What each column means is in the `score.py` docstring; the short version:

| Column | Meaning |
|---|---|
| `bound` | share of the drawn front length with one of our lines parallel (≤45°) and inside the slope-aware window: ≤150 km along, −150 to 150 + shift km across, + toward the cold air. Shift: `SLOPE_SHIFT_KM` (warm 100/220/450, cold 50/110/240 km at 925/850/700). |
| `false` | share of our line length matching no drawn front that way. |
| `type` (`n`) | on matched cold/warm cells away from junctions, how often our label is the drawn one. Junctions (within 150 km of a drawn front of another type) and occlusions are counted apart. |
| offset | median signed across-front distance, by type (`c`/`w`/`o`), km toward the cold side. |
| missed | per missed cell: `no_signal`, `fragmented` (passed but in pieces < 200 km), `kept_not_parallel` (on a line crossing the front), `gated:<gate>`, and `other_level` (matched at another level). |

Variants per level: `default`, `sensitive`, `strict`, `gap75` (75 km gaps bridged),
`motion` (cold/warm from which way the axis moves over 3 h, majority per line),
`multilevel` (default lines kept only where a parallel line exists at another level ≤300 km).
The table is followed by the default-gate **type flips** (grouped, junction or clear) and the
**METAR passages** (station, type, veer, ΔT/ΔTd, distance to each analysis and to our
925/850/700 lines per model).

## 3. Read (for every time scored in this run)

Open, with Read:
- `$C/charts/dwd/<T>/ana_w1600.png` — the DWD analysis;
- `$C/charts/mo/<T>/ana.gif` — Met Office, 00/12Z only (wide rotated N Atlantic chart:
  Europe sits right of centre, the Mediterranean bottom right);
- `$C/plots/<T>.png` — 3 levels × 3 models, default gate: DWD cells green = caught,
  black = missed (square cold, circle warm, triangle occluded), Met Office cyan, ours
  red/blue/purple (faint = no DWD match), METAR passages as orange C/W.

The scored domain is 36–58.5°N, 16.5°W–27°E. For each time, decide:

1. **Extraction OK?** Every front DWD draws inside the domain appears as cells in the plot,
   with the right type, and nothing else does. Troughs (black) are not scored by design.
   A front hidden under a label or legend, or a colour the mask missed, makes
   `extraction_ok: false`, and the time drops out of the tally.
2. **Regime**, a short phrase: e.g. "Atlantic wave, triple point Biscay", "slow
   Mediterranean warm front", "cut-off low Iberia", "summer, fronts weak/absent",
   "occlusion over North Sea".
3. **DWD vs Met Office**: `yes` / `partly` / `no`, and where they differ. The two analysts
   disagree more often than you'd expect (on 22 Nov 00Z the Met Office drew an occlusion
   over Denmark/S Sweden and DWD nothing). Where they differ, a miss against one is not
   evidence against us.
4. **Misses that matter**: `no_signal` at a level is the weather (that front isn't there at
   that level), not a calibration target. `gated:*` and `fragmented` are. A big
   `other_level` share means the front is at another level — say which.
5. **Type flips**: junction flips are expected; for each *clear* group, a likely cause from
   the plot: another parallel boundary nearby (coast, terrain edge, a second front), a
   slow-moving front (small |advection|), or a line whose majority label spans a junction.
6. **METAR passages**: plausible ones sit within ~150 km of a drawn front with the same
   type. Ones far from every front are likely diurnal or sea-breeze changes: note it if
   the detector looks wrong on this day. Passages near the Met Office front but not DWD's
   back the Met Office there.
7. **Keep as a showcase?** Name the case if it shows something well (e.g. "warm front at
   700 but not 850").

## 4. Record

Write one notes file per time to the scratch dir and record it:

```json
{"regime": "Atlantic wave, triple point Biscay; slow warm front to Sardinia",
 "dwd_mo_agree": "partly",
 "dwd_mo_where": "MO occlusion over Denmark/S Sweden, DWD none; MO warm front stops at Biscay",
 "extraction_ok": true,
 "exclude": false,
 "observations": ["850 misses over France are fragmented, not gated",
                  "GFS 850 labels the S Iberia cold front warm, clear of the junction"],
 "showcase": "warm front clean at 700, broken at 850"}
```

```bash
python $S/score.py record $RECORD $C 2026-11-22T00Z $W/notes_2026-11-22T00Z.json
```

`regime`, `dwd_mo_agree` and `extraction_ok` are required. Recording a time twice is refused;
`--replace` redoes it (e.g. after `score-all --rescore`); without a notes file it keeps the
notes already recorded.

## 5. Tally and report

```bash
python $S/score.py tally $RECORD                 # all; --since 2026-11-15, --ref mo
```

The tally prints, over all recorded times:
- boundary / false / type per model × level × variant, with the miss causes;
- the median signed offset by level and type against the expected shift (the measured slope);
- the best level per time and model, next to your regime note;
- flips by kind and their recurring places;
- METAR passages: distance to each analysis and to each model/level;
- METAR **lift**: share of passages within 150 km of each set of lines, against the share of
  all reporting stations that close at the same time (`base`). Lift 1 = no better than a
  random station; `z` is how far above chance (overstated, nearby stations aren't
  independent); `type` = our label matches the passage. Split all / cold / warm, plus the
  passages away from every DWD front (does the Met Office or a model explain them?).
  Use lift, not the raw share: a set that draws more lines gets more hits for free.

Report to the user, briefly:
- what is new in this run: times scored, anything notable from step 3;
- the scoreboard lines that moved;
- anything recurring: a flip place, a false-line hot spot, a level that keeps winning in
  one regime.

**What the numbers can and can't support:**
- A **gate change** needs at least two weeks of times and several regimes, and must win on
  boundary without a matching rise in false. One day is a hint. A change to the production
  presets or to the briefing's fronts advisory goes through `designs/meteorology-decisions.md`,
  as any calibration change does.
- **False alarms against DWD have a floor**: DWD draws fewer fronts than the θe field holds.
  Compare variants with each other, not against zero.
- **700 hPa against surface fronts** is meaningful only through the slope window. If the
  measured offsets settle away from `SLOPE_SHIFT_KM`, update the table and `score-all --rescore`.
- **METAR passage thresholds are a first guess** (`detect_passages`). Before quoting the
  METAR lines of the tally, check a few events against the charts.

## Files

- `scripts/fetch.py`: transport. Reuses `prod-briefings-review/scripts/fetch.py` helpers (ssh,
  container streaming, cleanup).
- `scripts/trim_snapshot.py`, `scripts/metar_dump.py`: copied into the container and run there.
- `scripts/score.py`: extraction, scoring, plots, record, tally.
- Library `$C`: ~45 MB a day (charts ~20, grids ~25, METARs ~5). It is the calibration set:
  keep it. Any new variant is scored on every past time with `score-all --rescore`.
